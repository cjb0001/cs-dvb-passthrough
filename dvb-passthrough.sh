#!/usr/bin/env bash

# Copyright (c) 2021-2026 community-scripts ORG / Chris
# License: MIT
# Description: Universal DVB TV Tuner Auto-Passthrough for Proxmox VE & Incus containers
# Hardware: any initialized kernel DVB character device (USB/PCIe).

set -Eeuo pipefail

msg_info() { printf '[INFO] %s\n' "$*"; }
msg_ok() { printf '[OK] %s\n' "$*"; }
msg_warn() { printf '[WARN] %s\n' "$*" >&2; }
msg_error() { printf '[ERROR] %s\n' "$*" >&2; }
# --- Error Trapping ---
catch_errors() {
  local rc=$?
  msg_error "Operation failed at line $1 (exit $rc). Review the diagnostics and any printed backup locations before retrying."
  exit "$rc"
}
trap 'catch_errors $LINENO' ERR

# Keep ownership outside the container configuration: Proxmox moves comments
# when it serializes a config. Pending entries make interrupted writes retryable.
write_tracking() {
  local destination=$1 temporary
  shift
  mkdir -p "$STATE" || return
  chmod 0700 "$STATE" || return
  temporary=$(mktemp "$STATE/.devices.XXXXXXXX") || return
  if (($#)); then
    printf '%s\n' "$@" > "$temporary" || return
  fi
  mv -- "$temporary" "$destination"
}

backup_tracking() {
  local filename
  for filename in devices devices.pending selection profile; do
    if [[ -f $STATE/$filename ]]; then
      cp -- "$STATE/$filename" "$BACKUP/tracked-$filename" || return
    fi
  done
}

pve_device_signature() {
  local value=${1#*:} option
  local -a options=()
  value=${value#"${value%%[![:space:]]*}"}
  IFS=',' read -r -a options <<< "$value"
  for option in "${options[@]}"; do
    # Proxmox also accepts the path as the unnamed first property.
    [[ $option != /dev/* ]] || option="path=$option"
    printf '%s\n' "$option"
  done | LC_ALL=C sort
}

pve_tracked_entries() {
  local configuration=$1 filename entries
  if [[ -f $STATE/devices || -f $STATE/devices.pending ]]; then
    for filename in devices devices.pending; do
      if [[ -f $STATE/$filename ]]; then cat -- "$STATE/$filename" || return; fi
    done
    return 0
  fi
  # Import only intact old blocks containing precisely the entries we emitted.
  # Never delete a region merely because it falls between two comments.
  entries=$(awk '
    $0=="# --- DVB TV Tuner Passthrough ---" {
      if (begins++ || ends) {bad=1; exit}; inside=1; next
    }
    $0=="# --- End DVB TV Tuner Passthrough ---" {
      if (!inside || ends++) {bad=1; exit}; inside=0; next
    }
    inside && NF {
      if ($0 !~ /^dev[0-9]+: path=\/dev\/(dvb\/adapter[0-9]+\/[a-zA-Z0-9_]+|media[0-9]+),uid=0,gid=[0-9]+,mode=0660$/) {
        bad=1; exit
      }
      print; found++
    }
    /^dev[0-9]+:.*\/dev\/(dvb\/|media[0-9]+)/ {outside++}
    END {
      if (bad || inside || begins!=ends) exit 1
      if (begins && !found && outside) exit 2
    }
  ' "$configuration") || {
    msg_error 'Cannot safely import legacy DVB tracking. Review the original config backup and restore verified device entries to the tracking manifest (see README).'
    return 1
  }
  [[ -z $entries ]] || printf '%s\n' "$entries"
  return 0
}

pve_clean_config() {
  local configuration=$1 entries entry key signature line
  local -A owned=() expected=()
  entries=$(pve_tracked_entries "$configuration") || return
  while IFS= read -r entry; do
    [[ -n $entry ]] || continue
    [[ $entry =~ ^dev([0-9]+):[[:space:]]+.+$ ]] || {
      msg_error 'Invalid Proxmox tracking manifest'; return 1;
    }
    key=${entry%%:*}
    signature=$(pve_device_signature "$entry") || return
    owned["$key"]=1
    expected["$key|$signature"]=1
  done <<< "$entries"
  while IFS= read -r line || [[ -n $line ]]; do
    case "$line" in
      '# --- DVB TV Tuner Passthrough ---'|'# --- End DVB TV Tuner Passthrough ---') continue ;;
    esac
    key=${line%%:*}
    if [[ -n $key && -n ${owned[$key]+present} ]]; then
      signature=$(pve_device_signature "$line") || return
      [[ -n ${expected["$key|$signature"]+present} ]] || {
        msg_error "Tracked entry $key was changed outside this script; review it before retrying"; return 1;
      }
      continue
    fi
    printf '%s\n' "$line"
  done < "$configuration"
}

pve_save_config() {
  local configuration=$1 entries
  shift
  local -a previous=()
  entries=$(pve_tracked_entries "$CONF_FILE") || return
  [[ -z $entries ]] || mapfile -t previous <<< "$entries"
  # Preserve imported and pending ownership before replacing either journal.
  write_tracking "$STATE/devices" "${previous[@]}" || return
  write_tracking "$STATE/devices.pending" "$@" || return
  [[ $(sha256sum "$CONF_FILE") == "$BEFORE" ]] || {
    msg_error 'CT config changed during planning'; return 1;
  }
  # pmxcfs: write the existing file; backups live outside /etc/pve.
  printf '%s\n' "$configuration" > "$CONF_FILE" || return
  pct config "$TARGET_CT" >/dev/null || return
  mv -- "$STATE/devices.pending" "$STATE/devices"
}

# Pin hardware operations to the local daemon and its selected project.
# Non-interactive exec preserves stdin transfers and avoids terminal CRLF output.
run_in_ct() {
  if [[ $PLATFORM == proxmox ]]; then pct exec "$TARGET_CT" -- "$@"
  else incus exec "$TARGET_CT" -T -- "$@"; fi
}

incus_target_context() {
  [[ $TARGET_CT =~ ^[a-zA-Z0-9][a-zA-Z0-9-]*$ ]] || { msg_error 'Invalid Incus name'; return 1; }
  INCUS_PROJECT=$(incus project get-current) || return
  [[ $INCUS_PROJECT =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || { msg_error 'Invalid Incus project'; return 1; }
  local instance type
  instance=$(incus list "^$TARGET_CT$" -c st --format csv) || return
  [[ -n $instance && $instance != *$'\n'* && $instance == *,* ]] || { msg_error 'Incus instance not found or ambiguous'; return 1; }
  CT_STATE=${instance%%,*}; type=${instance#*,}
  [[ $type == CONTAINER || $type == CONTAINER\ \(*\) ]] || { msg_error 'DVB unix-char passthrough requires an Incus container, not a VM'; return 1; }
  CT_RUNNING=false
  [[ $CT_STATE != RUNNING ]] || CT_RUNNING=true
  if [[ $INCUS_PROJECT == default ]]; then
    # Preserve ownership records from older default-project installations.
    STATE="/var/lib/dvb-passthrough/$TARGET_CT"
  else
    STATE="/var/lib/dvb-passthrough/incus/$INCUS_PROJECT/$TARGET_CT"
  fi
}

incus_expanded_device_names() {
  incus config show "$TARGET_CT" --expanded | awk '
    /^devices:$/ { devices=1; next }
    devices && /^[^ ]/ { exit }
    devices && /^  [a-zA-Z0-9_]+:$/ { sub(/^  /, ""); sub(/:$/, ""); print }
  '
}

load_incus_tracking() {
  local key
  local -A seen=()
  TRACKED_DEVICES=()
  [[ -f $STATE/devices ]] || return 0
  while IFS= read -r key || [[ -n $key ]]; do
    [[ $key =~ ^dvbpt_[a-zA-Z0-9_]+$ ]] || {
      msg_error 'Invalid Incus tracking manifest'; return 1;
    }
    if [[ -z ${seen[$key]+present} ]]; then
      TRACKED_DEVICES+=("$key")
      seen["$key"]=1
    fi
  done < "$STATE/devices"
}

remove_incus_device() {
  local key=$1 existing tracked
  local -a remaining=()
  existing=$(incus config device list "$TARGET_CT") || return
  if grep -qxF "$key" <<< "$existing"; then
    if ! incus config device remove "$TARGET_CT" "$key"; then
      # An API timeout may report failure after the server applied the removal.
      existing=$(incus config device list "$TARGET_CT") || return
      if grep -qxF "$key" <<< "$existing"; then
        msg_error "Could not remove tracked device $key; retry the operation"; return 1
      fi
    fi
  fi
  for tracked in "${TRACKED_DEVICES[@]}"; do
    [[ $tracked == "$key" ]] || remaining+=("$tracked")
  done
  write_tracking "$STATE/devices" "${remaining[@]}" || return
  TRACKED_DEVICES=("${remaining[@]}")
}

incus_validate_unmanaged_sources() {
  local configuration sources source
  load_incus_tracking || return
  configuration=$(incus config show "$TARGET_CT" --expanded) || return
  sources=$(dvb_incus_device_sources "${TRACKED_DEVICES[*]}" <<< "$configuration") || return
  while IFS= read -r source; do
    [[ -z $source ]] || dvb_check_source "$source" 'unmanaged target configuration' || return
  done <<< "$sources"
}

configure_incus_devices() {
  local existing expanded dev key tracked entry
  local -a desired=()
  load_incus_tracking || return
  existing=$(incus config device list "$TARGET_CT") || return
  expanded=$(incus_expanded_device_names) || return
  # Validate every collision before modifying any device.
  for dev in "${PASSTHROUGH_DEVICES[@]}"; do
    key="dvbpt_${dev#/dev/}"; key=${key//\//_}
    desired+=("$key")
    if grep -qxF "$key" <<< "$expanded" && ! grep -qxF "$key" <<< "$existing"; then
      msg_error "Inherited profile device collision: $key"; return 1
    fi
    if grep -qxF "$key" <<< "$existing"; then
      tracked=false
      for entry in "${TRACKED_DEVICES[@]}"; do
        [[ $entry != "$key" ]] || tracked=true
      done
      $tracked || { msg_error "Unmanaged device collision: $key"; return 1; }
    fi
  done
  for key in "${TRACKED_DEVICES[@]}"; do
    if ! printf '%s\n' "${desired[@]}" | grep -qxF "$key"; then
      remove_incus_device "$key" || return
    fi
  done
  for dev in "${PASSTHROUGH_DEVICES[@]}"; do
    key="dvbpt_${dev#/dev/}"; key=${key//\//_}
    if grep -qxF "$key" <<< "$existing"; then
      incus config device set "$TARGET_CT" "$key" "source=$dev" "path=$dev" uid=0 "gid=$CT_GID" mode=0660 required=false || return
    else
      # Journal before adding so an interruption cannot leave an untracked node.
      tracked=false
      for entry in "${TRACKED_DEVICES[@]}"; do
        [[ $entry != "$key" ]] || tracked=true
      done
      if ! $tracked; then
        TRACKED_DEVICES+=("$key")
        write_tracking "$STATE/devices" "${TRACKED_DEVICES[@]}" || return
      fi
      incus config device add "$TARGET_CT" "$key" unix-char "source=$dev" "path=$dev" uid=0 "gid=$CT_GID" mode=0660 required=false || return
    fi
  done
}

# Runtime firmware only; these are immutable LibreELEC snapshots, not driver code
# or flash updates. Source/verification records are documented in docs/design.md.
firmware_catalog() {
  local filename=$1 revision=90261ae2934329f6ca84dd6c72d10d0777bf4b0e
  case "$filename" in
    dvb-demod-si2168-b40-01.fw)
      FW_DRIVER=si2168; FW_SHA256=d25c7deb9f69dca232ce25ab108da8ff5013d6d39088e0ec3475d97ded8af718 ;;
    dvb-demod-si2168-d60-01.fw)
      FW_DRIVER=si2168; FW_SHA256=5e1c860e95a8d92b2716b0075dd00e55f99c2c368e35340f6b527c366ff27ee7 ;;
    dvb-tuner-si2157-a30-01.fw|dvb_driver_si2157_rom50.fw)
      FW_DRIVER=si2157; FW_SHA256=8b2af7f9714834106adc075bde60433a1f6d6812383e8c3f0d4e50212fb3eb5e ;;
    dvb-demod-m88ds3103.fw)
      FW_DRIVER=m88ds3103; FW_SHA256=4767ab80ceba4a66315cbec2a07ae1f7ebbd19c5758fd098b932e02c9108eff9 ;;
    dvb-demod-m88ds3103b.fw)
      FW_DRIVER=m88ds3103; FW_SHA256=4336f79c0c706162f5e5bb65351ca4f24d1de10b9d908c427edfe339d843f7ac ;;
    *) return 1 ;;
  esac
  FW_URL="https://raw.githubusercontent.com/LibreELEC/dvb-firmware/$revision/firmware/$filename"
  FW_LICENSE='LibreELEC package: LicenseRef-Free-to-use; no separate per-file license supplied for these blobs'
}

firmware_requests() {
  local line driver device message filename key
  local -A failures=()
  local prefix='^([a-zA-Z0-9_]+)[[:space:]]+([0-9]+-[0-9a-fA-F]{4}):[[:space:]]+(.*)$'
  local direct='Direct firmware load for ([^[:space:]]+) failed with error -2([[:space:]]|$)'
  local missing="firmware file '([^']+)' not found"
  while IFS= read -r line; do
    [[ $line =~ $prefix ]] || continue
    driver=${BASH_REMATCH[1]}; device=${BASH_REMATCH[2]}; message=${BASH_REMATCH[3]}
    case "$driver" in si2168|si2157|m88ds3103) ;; *) continue ;; esac
    if [[ $message == *'firmware version:'* ]]; then
      # A later successful initialization, including a working fallback file,
      # supersedes earlier missing-file messages for this exact client.
      for key in "${!failures[@]}"; do
        if [[ $key == "$driver"$'\t'"$device"$'\t'* ]]; then unset 'failures[$key]'; fi
      done
      continue
    fi
    if [[ $message =~ $direct || $message =~ $missing ]]; then
      filename=${BASH_REMATCH[1]}
      [[ $filename =~ ^[a-zA-Z0-9_-][a-zA-Z0-9._-]*\.fw$ ]] || continue
      failures["$driver"$'\t'"$device"$'\t'"$filename"]=1
    fi
  done
  for key in "${!failures[@]}"; do printf '%s\n' "$key"; done
  return 0
}

resolve_firmware_device() {
  local driver=$1 device=$2 sys_root=${SYS_ROOT:-/sys} actual root vendor product revision
  FW_DEVICE=$(readlink -e "$sys_root/bus/i2c/devices/$device") || return 1
  [[ $FW_DEVICE == "$sys_root/devices/"* && -r $FW_DEVICE/name ]] || return 1
  if [[ -L $FW_DEVICE/driver ]]; then
    actual=$(readlink -e "$FW_DEVICE/driver") || return 1
    actual=${actual##*/}
  else
    actual=$(cat "$FW_DEVICE/name") || return 1
    # The I2C client name can include the chip suffix while its driver does not.
    case "$actual" in m88ds3103b|m88ds3103c) actual=m88ds3103 ;; esac
  fi
  [[ $actual == "$driver" ]] || return 1
  root=$FW_DEVICE
  while [[ $root != "$sys_root" && $root != / ]]; do
    if [[ -r $root/idVendor && -r $root/idProduct && -r $root/bcdDevice ]]; then
      vendor=$(cat "$root/idVendor"); product=$(cat "$root/idProduct"); revision=$(cat "$root/bcdDevice")
      [[ $vendor$product$revision =~ ^[0-9a-fA-F]{12}$ ]] || return 1
      FW_ROOT=$root; FW_ID="USB $vendor:$product revision $revision"
      return 0
    fi
    if [[ -L $root/subsystem && $(readlink -e "$root/subsystem") == "$sys_root/bus/pci" ]]; then
      vendor=$(cat "$root/vendor"); product=$(cat "$root/device"); revision=$(cat "$root/revision")
      local subvendor subdevice
      subvendor=$(cat "$root/subsystem_vendor"); subdevice=$(cat "$root/subsystem_device")
      [[ $vendor$product$subvendor$subdevice =~ ^(0x[0-9a-fA-F]{4}){4}$ && $revision =~ ^0x[0-9a-fA-F]{2}$ ]] || return 1
      FW_ROOT=$root; FW_ID="PCI $vendor:$product subsystem $subvendor:$subdevice revision $revision"
      return 0
    fi
    root=${root%/*}
  done
  return 1
}

firmware_present() {
  local filename=$1 directory=${FIRMWARE_DIR:-/lib/firmware} release path suffix custom=''
  release=$(uname -r) || return
  if [[ -r ${SYS_ROOT:-/sys}/module/firmware_class/parameters/path ]]; then
    custom=$(cat "${SYS_ROOT:-/sys}/module/firmware_class/parameters/path") || return
  fi
  for path in "$custom" "$directory/updates/$release" "$directory/updates" "$directory/$release" "$directory"; do
    [[ -n $path ]] || continue
    for suffix in '' .xz .zst .gz; do
      # Preserve existing files, including symlinks that need manual repair.
      if [[ -e $path/$filename$suffix || -L $path/$filename$suffix ]]; then return 0; fi
    done
  done
  return 1
}

install_firmware_blob() (
  local filename=$1 identity=$2 directory=${FIRMWARE_DIR:-/lib/firmware}
  local backup_root=${FIRMWARE_BACKUP_ROOT:-/var/backups/dvb-passthrough} temporary='' backup digest
  firmware_catalog "$filename" || { msg_error "No verified source for $filename"; return 1; }
  if firmware_present "$filename"; then
    msg_warn "Existing firmware preserved: $filename"; return 0
  fi
  mkdir -p "$directory" || return
  temporary=$(mktemp "$directory/.dvb-firmware.XXXXXXXX") || return
  trap 'rm -f -- "$temporary"' EXIT
  curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
    --tlsv1.2 --connect-timeout 15 --max-time 120 --max-filesize 1048576 --retry 2 \
    --output "$temporary" "$FW_URL" || return
  digest=$(sha256sum "$temporary") || return
  [[ -s $temporary && ${digest%% *} == "$FW_SHA256" ]] || {
    msg_error "Firmware checksum verification failed: $filename"; return 1;
  }
  mkdir -p "$backup_root" || return
  backup=$(mktemp -d "$backup_root/firmware.XXXXXXXX") || return
  chmod 0700 "$backup" || return
  cp -- "$temporary" "$backup/$filename" || return
  printf 'file=%s\nsource=%s\nsha256=%s\nhardware=%s\nlicense=%s\n' \
    "$filename" "$FW_URL" "$FW_SHA256" "$identity" "$FW_LICENSE" > "$backup/receipt.txt" || return
  chmod 0644 "$temporary" || return
  # Hard-link publication is atomic and refuses to overwrite even a dangling
  # symlink or a file created by a package manager during the download.
  ln -- "$temporary" "$directory/$filename" || {
    msg_error "Firmware destination changed; existing entry preserved: $filename"; return 1;
  }
  msg_ok "Installed verified runtime firmware: $directory/$filename"
  msg_info "Firmware receipt and verified copy: $backup"
)

firmware_workflow() {
  local logs requests driver device filename answer original_id original_root found=false installed=false unresolved=false
  local -A seen=()
  command -v journalctl >/dev/null || { msg_error 'Firmware diagnosis requires journalctl'; return 1; }
  logs=$(journalctl --dmesg --boot=0 --since="$FIRMWARE_SINCE" --no-pager --output=cat) || return
  requests=$(printf '%s\n' "$logs" | firmware_requests | sort -u) || return
  msg_info "Firmware diagnosis: current boot, since $FIRMWARE_SINCE"
  msg_info 'Prefer firmware provided by compatible host packages. This command does not install packages or change repositories.'
  if $INSTALL_FIRMWARE && ! $DRY_RUN; then
    command -v curl >/dev/null || { msg_error 'Firmware downloads require curl'; return 1; }
    umask 077
    exec 9>"${OPERATION_LOCK:-/run/lock/dvb-passthrough.lock}"
    flock -n 9 || { msg_error 'Another DVB operation is running'; return 1; }
  fi
  while IFS=$'\t' read -r driver device filename <&3; do
    [[ -n $filename ]] || continue
    if ! resolve_firmware_device "$driver" "$device"; then
      msg_warn "Unresolved or detached hardware: $driver $device requested $filename; manual review required"
      unresolved=true; continue
    fi
    found=true
    msg_info "$FW_ID at $FW_ROOT ($driver $device) requested $filename"
    if firmware_present "$filename"; then
      msg_info "Existing firmware preserved: $filename (the recorded failure may already be resolved)"; continue
    fi
    if ! firmware_catalog "$filename" || [[ $FW_DRIVER != "$driver" ]]; then
      msg_warn "No verified download mapping for $driver / $filename; resolve manually"
      unresolved=true; continue
    fi
    printf '  Source: %s\n  SHA-256: %s\n  Destination: %s/%s\n  License metadata: %s\n' \
      "$FW_URL" "$FW_SHA256" "${FIRMWARE_DIR:-/lib/firmware}" "$filename" "$FW_LICENSE"
    [[ -z ${seen[$filename]+present} ]] || continue
    seen["$filename"]=1
    if ! $INSTALL_FIRMWARE || $DRY_RUN; then continue; fi
    original_id=$FW_ID; original_root=$FW_ROOT
    if ! $ASSUME_YES; then
      read -r -p "Download and install $filename on the host? [y/N] " answer || return 1
      if [[ $answer != y && $answer != Y ]]; then continue; fi
    fi
    resolve_firmware_device "$driver" "$device" && [[ $FW_ID == "$original_id" && $FW_ROOT == "$original_root" ]] || {
      msg_error 'Hardware changed during firmware planning; rerun diagnosis'; return 1;
    }
    install_firmware_blob "$filename" "$FW_ID at $FW_ROOT ($driver $device)" || return
    installed=true
  done 3<<< "$requests"
  if ! $found; then
    msg_info 'No missing-firmware request could be matched to supported attached hardware. This does not establish that tuners are healthy.'
  fi
  if $installed; then
    msg_warn 'Firmware files installed; hardware initialization is not verified. Reconnect the affected USB tuner when idle, or schedule PCIe/host reinitialization, then rerun passthrough.'
  elif ! $INSTALL_FIRMWARE; then
    msg_info 'Diagnosis only. Use --install-firmware to review and confirm supported downloads.'
  fi
  if $INSTALL_FIRMWARE && $unresolved; then return 1; fi
}

# BEGIN shared physical tuner selection
dvb_selection_error() { printf '[ERROR] %s\n' "$*" >&2; }

# Data files only: never source host tracking files as shell code.
dvb_assignment_records() {
  local root=${dvb_state_root:-${STABLE_ROOT:-/var/lib/dvb-passthrough}} file directory id _slot vid pid serial _rest
  local -a files=("$root"/proxmox/*/selection "$root"/incus/*/*/selection "$root"/*/selection)
  files+=("$root"/proxmox/*/stable-map)
  for file in "${files[@]}"; do
    [[ -f $file ]] || continue
    directory=${file%/*}
    [[ $directory != "${STATE:-}" ]] || continue
    if [[ $file == */stable-map ]]; then
      [[ ! -f $directory/selection ]] || continue
      while read -r _slot vid pid serial _rest; do
        [[ $vid =~ ^[a-f0-9]{4}$ && $pid =~ ^[a-f0-9]{4}$ && $serial =~ ^[a-zA-Z0-9_.:-]+$ ]] || {
          dvb_selection_error "Invalid assignment map: $file"
          return 1
        }
        printf '%s\tusb:%s:%s:%s\n' "$directory" "$vid" "$pid" "$serial"
      done <"$file"
    else
      while IFS= read -r id || [[ -n $id ]]; do
        [[ $id == usb:* || $id == path:* ]] || {
          dvb_selection_error "Invalid assignment selection: $file"
          return 1
        }
        printf '%s\t%s\n' "$directory" "$id"
      done <"$file"
    fi
  done
}

dvb_assignment_matches() {
  local id=$1 index=$2
  [[ $id == "${dvb_tuner_keys[$index]}" || -n ${dvb_tuner_ids[$index]} && $id == "${dvb_tuner_ids[$index]}" ]]
}

dvb_check_source() {
  local source=$1 owner=$2 node resolved number major minor
  if [[ $source == @* ]]; then
    [[ $source =~ ^@([0-9]+):([0-9]+)$ ]] || {
      dvb_selection_error "Review numeric character-device mapping in $owner"
      return 1
    }
    major=${BASH_REMATCH[1]}
    minor=${BASH_REMATCH[2]}
    for node in "${dvb_selected_nodes[@]}"; do
      [[ -c $node ]] || continue
      number=$(stat -Lc '%t:%T' -- "$node") || return
      printf -v number '%d:%d' "0x${number%:*}" "0x${number#*:}"
      if [[ $number == "$major:$minor" ]]; then
        dvb_selection_error "$owner already exposes selected device $node as $source"
        return 1
      fi
    done
    return 0
  fi
  [[ $source != '?unix-char' ]] || {
    dvb_selection_error "Review source-less character-device mapping in $owner"
    return 1
  }
  [[ $source == /* ]] || return 0
  [[ $source != *\\* && $source != *'*'* && $source != *'?'* && $source != *'['* ]] || {
    dvb_selection_error "Review escaped or wildcard device source in $owner: $source"
    return 1
  }
  if [[ $source == /dev/dvb/* || $source == /dev/media[0-9]* ]]; then
    [[ -e $source ]] || {
      dvb_selection_error "Cannot resolve old DVB device mapping in $owner: $source; review its ownership first"
      return 1
    }
  fi
  resolved=$(readlink -m -- "$source") || return
  for node in "${dvb_selected_nodes[@]}"; do
    # Directory mounts expose descendants, including not-yet-created adapters.
    if [[ $node == "$source" || $node == "$source/"* || $node == "$resolved" || $node == "$resolved/"* ]]; then
      dvb_selection_error "$owner already exposes selected device $node via $source; release it and stop/start the source container first"
      return 1
    fi
    if [[ -c $source && -c $node ]]; then
      number=$(stat -Lc '%t:%T' -- "$source") || return
      if [[ $number == "$(stat -Lc '%t:%T' -- "$node")" ]]; then
        dvb_selection_error "$owner already exposes selected device $node via $source; release it and stop/start the source container first"
        return 1
      fi
    fi
  done
}

dvb_check_runtime() {
  local numbers=$1 owner=$2 number node
  while IFS= read -r number; do
    [[ $number =~ ^[a-fA-F0-9]+:[a-fA-F0-9]+$ ]] || {
      dvb_selection_error "Invalid runtime device inventory from $owner"
      return 1
    }
    for node in "${dvb_selected_nodes[@]}"; do
      if [[ -c $node && $(stat -Lc '%t:%T' -- "$node") == "$number" ]]; then
        dvb_selection_error "$owner still has a live node for $node; stop/start the source container before reassignment"
        return 1
      fi
    done
  done <<<"$numbers"
}

# The emitted script only stats nodes; it never opens/tunes a frontend.
dvb_runtime_inventory() {
  printf '%s\n' 'for node in /dev/dvb/adapter*/* /dev/media*; do [ ! -c "$node" ] || stat -Lc "%t:%T" "$node" || exit; done; exit 0'
}

dvb_check_pve_file() {
  local file=$1 owner=$2 line value source option
  local -a options=()
  while IFS= read -r line; do
    [[ $line != \[* ]] || break
    if [[ $line =~ ^dev[0-9]+:[[:space:]]*(.*) ]]; then
      value=${BASH_REMATCH[1]}
      IFS=',' read -r -a options <<<"$value"
      for option in "${options[@]}"; do
        option=${option#"${option%%[![:space:]]*}"}
        source=${option#path=}
        dvb_check_source "$source" "$owner" || return
      done
    elif [[ $line == lxc.mount.entry:* ]]; then
      read -r source value <<<"${line#*:}"
      dvb_check_source "$source" "$owner" || return
    fi
  done <"$file"
}

dvb_check_pve_assignments() {
  local target=$1 directory=${dvb_pve_config_dir:-/etc/pve/lxc} file ct status numbers
  for file in "$directory"/*.conf; do
    [[ -f $file ]] || continue
    ct=${file##*/}
    ct=${ct%.conf}
    [[ $ct != "$target" ]] || continue
    dvb_check_pve_file "$file" "Proxmox CT $ct" || return
    status=$(pct status "$ct") || return
    if [[ $status == 'status: running' ]]; then
      numbers=$(pct exec "$ct" -- sh -c "$(dvb_runtime_inventory)") || {
        dvb_selection_error "Cannot inspect live device ownership in CT $ct"
        return 1
      }
      [[ -z $numbers ]] || dvb_check_runtime "$numbers" "Proxmox CT $ct" || return
    fi
  done
}

# Bypass the standalone wrapper's pinned project, but always use the local daemon.
dvb_incus_query() { command incus --force-local "$@"; }

# shellcheck disable=SC2120 # Standalone target validation supplies excluded managed keys.
dvb_incus_device_sources() {
  awk -v excluded="${1:-}" '
    function scalar(line) {sub(/^    [^:]+: */, "", line); gsub(/^["\047]|["\047]$/, "", line); return line}
    function emit() {
      if (!skipped && source != "" && source != "null") print source
      else if (!skipped && type == "unix-char") {
        if (major ~ /^[0-9]+$/ && minor ~ /^[0-9]+$/) print "@" major ":" minor
        else print "?unix-char"
      }
      source=type=major=minor=""
      skipped=0
    }
    /^devices:$/ {in_devices=1; next}
    in_devices && /^[^ ]/ {emit(); in_devices=0}
    in_devices && /^  [^ ]/ {emit(); name=$0; sub(/^  /, "", name); sub(/:$/, "", name); skipped=index(" " excluded " ", " " name " ") > 0}
    in_devices && /^    source:/ {source=scalar($0)}
    in_devices && /^    type:/ {type=scalar($0)}
    in_devices && /^    major:/ {major=scalar($0)}
    in_devices && /^    minor:/ {minor=scalar($0)}
    END {if (in_devices) emit()}'
}

dvb_check_incus_assignments() {
  local target=$1 selected_project=${INCUS_PROJECT:-${INCUS_PROJECT_NAME:-default}} projects project instances ct type config sources source numbers status
  projects=$(dvb_incus_query project list -c n --format csv) || return
  while IFS= read -r project; do
    [[ $project =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || return 1
    instances=$(dvb_incus_query --project "$project" list -c nts --format csv) || return
    while IFS=, read -r ct type status; do
      [[ -n $ct && $type == CONTAINER* ]] || continue
      [[ $project != "$selected_project" || $ct != "$target" ]] || continue
      config=$(dvb_incus_query --project "$project" config show "$ct" --expanded) || return
      # Incus emits absolute device source paths as YAML scalars. Reject quoted
      # escape sequences rather than guessing their decoded meaning.
      sources=$(dvb_incus_device_sources <<<"$config") || return
      while IFS= read -r source; do
        [[ -n $source ]] || continue
        [[ $source != *\\* ]] || {
          dvb_selection_error "Review escaped device source in Incus $project/$ct"
          return 1
        }
        source=${source#\"}
        source=${source%\"}
        source=${source#\'}
        source=${source%\'}
        dvb_check_source "$source" "Incus $project/$ct" || return
      done <<<"$sources"
      if [[ $status == RUNNING ]]; then
        numbers=$(dvb_incus_query --project "$project" exec "$ct" -- sh -c "$(dvb_runtime_inventory)") || return
        [[ -z $numbers ]] || dvb_check_runtime "$numbers" "Incus $project/$ct" || return
      fi
    done <<<"$instances"
  done <<<"$projects"
}

dvb_assignment_preflight() {
  local platform=$1 target=$2 records owner id i
  records=$(dvb_assignment_records) || return
  while IFS=$'\t' read -r owner id; do
    [[ -n $owner ]] || continue
    for i in "${dvb_selected_indices[@]}"; do
      if dvb_assignment_matches "$id" "$i"; then
        dvb_selection_error "${dvb_tuner_labels[$i]} is assigned to $owner; release the source assignment first"
        return 1
      fi
    done
  done <<<"$records"
  case "$platform" in
  proxmox) dvb_check_pve_assignments "$target" ;;
  incus) dvb_check_incus_assignments "$target" ;;
  *)
    dvb_selection_error "Unknown assignment platform: $platform"
    return 1
    ;;
  esac
}

dvb_assignment_labels() {
  local records owner id i root=${dvb_state_root:-${STABLE_ROOT:-/var/lib/dvb-passthrough}}
  local -A labelled=()
  records=$(dvb_assignment_records) || return
  while IFS=$'\t' read -r owner id; do
    [[ -n $owner ]] || continue
    for i in "${!dvb_tuner_roots[@]}"; do
      if dvb_assignment_matches "$id" "$i" && [[ -z ${labelled["$i:$owner"]+x} ]]; then
        dvb_tuner_labels[$i]+=" [Assigned: ${owner#"$root"/}]"
        labelled["$i:$owner"]=1
      fi
    done
  done <<<"$records"
}

# Called under the same host lock as assignment changes. Build the union before
# replacing the managed file, so a split deployment retains both power policies.
# shellcheck disable=SC2120 # Standalone removal supplies false; creation defaults to true.
dvb_power_update() {
  local include_current=${1:-true} rules=${dvb_power_rules:-/etc/udev/rules.d/99-tvtuner-power.rules} records owner id i physical vid pid port temporary _prefix serial line matches backup backup_root
  local -A selected=() wanted=() ruleset=()
  [[ ! -e $rules ]] || grep -qE '^# (Generated: USB autosuspend|Automatically generated:.*[Tt]uner)' "$rules" || {
    dvb_selection_error "Unrecognized power rule file: $rules"
    return 1
  }
  if [[ $include_current == true ]]; then
    for i in "${dvb_selected_indices[@]}"; do
      selected[$i]=1
      wanted[${dvb_tuner_ids[$i]:-${dvb_tuner_keys[$i]}}]=1
    done
  fi
  # Keep the previous target selection too until its config has been committed.
  records=$(STATE='' dvb_assignment_records) || return
  while IFS=$'\t' read -r owner id; do
    [[ -n $owner ]] || continue
    wanted[$id]=1
    for i in "${!dvb_tuner_roots[@]}"; do
      if dvb_assignment_matches "$id" "$i"; then selected[$i]=1; fi
    done
  done <<<"$records"
  temporary=$(mktemp "${rules}.XXXXXXXX") || return
  printf '%s\n' '# Generated: USB autosuspend disabled only for detected DVB tuner USB devices' >"$temporary"
  for id in "${!wanted[@]}"; do
    if [[ $id == usb:* ]]; then
      IFS=: read -r _prefix vid pid serial <<<"$id"
      [[ $vid =~ ^[a-f0-9]{4}$ && $pid =~ ^[a-f0-9]{4}$ && $serial =~ ^[a-zA-Z0-9_.:-]+$ ]] || {
        rm -f -- "$temporary"
        return 1
      }
      matches=0
      for i in "${!dvb_tuner_roots[@]}"; do
        physical=${dvb_tuner_roots[$i]}
        if [[ ${dvb_tuner_serials[$i]} == "$serial" && -f $physical/idVendor && -f $physical/idProduct &&
          $(cat "$physical/idVendor") == "$vid" && $(cat "$physical/idProduct") == "$pid" ]]; then matches=$((matches + 1)); fi
      done
      if ((matches > 1)); then
        rm -f -- "$temporary"
        dvb_selection_error "Ambiguous saved USB power identity: $id"
        return 1
      fi
      printf -v line 'ACTION=="add|change", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="%s", ATTR{idProduct}=="%s", ATTR{serial}=="%s", ATTR{power/control}="on"' "$vid" "$pid" "$serial"
      ruleset[$line]=1
    elif [[ $id == path:* && -f $rules ]]; then
      # Keep a previously verified port rule for a disconnected path-identified
      # USB device. Only the exact rule format emitted by this utility is kept.
      port=${id#path:}
      port=${port##*/}
      if [[ $port =~ ^[0-9.-]+$ ]]; then
        while IFS= read -r line; do
          if [[ $line == *"KERNEL==\"$port\""* && $line =~ ^ACTION==\"add\|change\",\ SUBSYSTEM==\"usb\",\ ENV\{DEVTYPE\}==\"usb_device\",\ KERNEL==\"[0-9.-]+\",\ ATTR\{idVendor\}==\"[a-fA-F0-9]{4}\",\ ATTR\{idProduct\}==\"[a-fA-F0-9]{4}\",\ ATTR\{power/control\}=\"on\"$ ]]; then ruleset[$line]=1; fi
        done <"$rules"
      fi
    fi
  done
  for i in "${!selected[@]}"; do
    physical=${dvb_tuner_roots[$i]}
    [[ -f $physical/idVendor && -f $physical/idProduct ]] || continue
    vid=$(cat "$physical/idVendor")
    pid=$(cat "$physical/idProduct")
    port=${physical##*/}
    [[ $vid =~ ^[a-fA-F0-9]{4}$ && $pid =~ ^[a-fA-F0-9]{4}$ && $port =~ ^[0-9.-]+$ ]] || {
      rm -f -- "$temporary"
      return 1
    }
    if [[ -z ${dvb_tuner_ids[$i]} ]]; then
      # A path selection follows physical topology rather than an unusable or
      # duplicate serial. Replace its previous port rule with current IDs.
      for line in "${!ruleset[@]}"; do [[ $line != *"KERNEL==\"$port\""* ]] || unset 'ruleset[$line]'; done
      printf -v line 'ACTION=="add|change", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", KERNEL=="%s", ATTR{idVendor}=="%s", ATTR{idProduct}=="%s", ATTR{power/control}="on"' "$port" "$vid" "$pid"
      ruleset[$line]=1
    fi
    if [[ -f $physical/power/control ]]; then
      printf 'on\n' >"$physical/power/control" || {
        rm -f -- "$temporary"
        return 1
      }
      [[ $(cat "$physical/power/control") == on ]] || {
        rm -f -- "$temporary"
        dvb_selection_error "USB power policy verification failed for $physical"
        return 1
      }
    fi
  done
  if ((${#ruleset[@]})); then printf '%s\n' "${!ruleset[@]}" | LC_ALL=C sort >>"$temporary"; fi
  if [[ -f $rules ]] && ! cmp -s "$temporary" "$rules"; then
    backup_root=${dvb_power_backup_root:-/var/backups/dvb-passthrough}
    mkdir -p "$backup_root" || {
      rm -f -- "$temporary"
      return 1
    }
    backup=$(mktemp -d "$backup_root/power.XXXXXXXX") || {
      rm -f -- "$temporary"
      return 1
    }
    cp -p -- "$rules" "$backup/" || {
      rm -f -- "$temporary"
      return 1
    }
    printf 'DVB power rules backup: %s\n' "$backup"
  fi
  chmod 0644 "$temporary" && mv -f -- "$temporary" "$rules" || {
    rm -f -- "$temporary"
    return 1
  }
  udevadm control --reload-rules
}

dvb_discover_tuners() {
  local sys=${SYS_ROOT:-/sys} dev=${DEV_ROOT:-/dev} cls name adapter component node physical index label serial vid pid i key
  local -A indices=() seen_adapters=() serial_counts=()
  dvb_tuner_roots=()
  dvb_tuner_keys=()
  dvb_tuner_ids=()
  dvb_tuner_serials=()
  dvb_tuner_labels=()
  dvb_tuner_nodes=()
  dvb_tuner_adapters=()
  for cls in "$sys"/class/dvb/*; do
    name=${cls##*/}
    [[ $name =~ ^dvb([0-9]+)\.([a-zA-Z0-9_]+)$ ]] || continue
    adapter=${BASH_REMATCH[1]}
    component=${BASH_REMATCH[2]}
    node="$dev/dvb/adapter$adapter/$component"
    [[ -c $node && -e $cls/device ]] || continue
    physical=$(readlink -f "$cls/device") || return
    while [[ $physical != "$sys" && $physical != / ]]; do
      [[ ! -f $physical/idVendor || ! -f $physical/idProduct ]] || break
      if [[ -L $physical/subsystem && $(readlink -f "$physical/subsystem") == "$sys/bus/pci" ]]; then break; fi
      physical=${physical%/*}
    done
    [[ $physical != "$sys" && $physical != / ]] || continue
    if [[ -z ${indices[$physical]+x} ]]; then
      index=${#dvb_tuner_roots[@]}
      indices[$physical]=$index
      dvb_tuner_roots+=("$physical")
      dvb_tuner_keys+=("path:${physical#"$sys"/devices/}")
      serial=''
      key=''
      if [[ -f $physical/idVendor && -f $physical/idProduct ]]; then
        vid=$(cat "$physical/idVendor")
        pid=$(cat "$physical/idProduct")
        label="USB $vid:$pid (${physical##*/})"
        [[ ! -f $physical/product ]] || label="$(cat "$physical/product") — $label"
        if [[ -f $physical/serial ]]; then
          serial=$(cat "$physical/serial")
          if [[ $vid =~ ^[0-9a-f]{4}$ && $pid =~ ^[0-9a-f]{4}$ && $serial =~ ^[a-zA-Z0-9_.:-]+$ ]]; then
            key="usb:$vid:$pid:$serial"
            serial_counts[$key]=$((${serial_counts[$key]:-0} + 1))
            label+=" — serial $serial"
          else serial=''; fi
        fi
      else
        label="PCI ${physical##*/}"
        [[ ! -f $physical/vendor || ! -f $physical/device ]] || label+=" $(cat "$physical/vendor"):$(cat "$physical/device")"
      fi
      dvb_tuner_labels+=("$label")
      dvb_tuner_ids+=("$key")
      dvb_tuner_serials+=("$serial")
      dvb_tuner_nodes+=('')
      dvb_tuner_adapters+=('')
    else index=${indices[$physical]}; fi
    dvb_tuner_nodes[$index]+="$node "
    if [[ -z ${seen_adapters["$physical:$adapter"]+x} ]]; then
      seen_adapters["$physical:$adapter"]=1
      dvb_tuner_adapters[$index]+="${dvb_tuner_adapters[$index]:+,}$adapter"
    fi
  done
  # Related media nodes inherit the same physical-device selection.
  for cls in "$sys"/class/media/*; do
    node="$dev/${cls##*/}"
    [[ -c $node && -e $cls/device ]] || continue
    physical=$(readlink -f "$cls/device") || return
    for i in "${!dvb_tuner_roots[@]}"; do
      [[ $physical != "${dvb_tuner_roots[$i]}" && $physical != "${dvb_tuner_roots[$i]}/"* ]] || dvb_tuner_nodes[$i]+="$node "
    done
  done
  # Duplicate serials remain selectable by physical path, never ambiguously.
  for i in "${!dvb_tuner_ids[@]}"; do
    key=${dvb_tuner_ids[$i]}
    [[ -z $key || ${serial_counts[$key]} == 1 ]] || dvb_tuner_ids[$i]=''
  done
  dvb_assignment_labels
}

dvb_resolve_selection() {
  local request i found
  local -A seen=()
  dvb_selected_indices=()
  for request in "$@"; do
    found=''
    for i in "${!dvb_tuner_roots[@]}"; do
      if [[ $request == "${dvb_tuner_keys[$i]}" ||
        -n ${dvb_tuner_ids[$i]} && $request == "${dvb_tuner_ids[$i]}" ||
        -n ${dvb_tuner_serials[$i]} && $request == "${dvb_tuner_serials[$i]}" ]]; then
        [[ -z $found ]] || {
          dvb_selection_error "Ambiguous tuner '$request'; use its path ID"
          return 1
        }
        found=$i
      fi
    done
    [[ -n $found ]] || {
      dvb_selection_error "Selected tuner is unavailable: $request"
      return 1
    }
    if [[ -z ${seen[$found]+x} ]]; then
      dvb_selected_indices+=("$found")
      seen[$found]=1
    fi
  done
}

dvb_select_tuners() {
  local i n reply flag saved_request defaults_ok=true checklist=false
  local -a requests=("$@") defaults=() choices=() menu=() labels=()
  local -A saved=()
  if [[ ${dvb_select_all:-false} == true ]]; then
    dvb_selected_indices=("${!dvb_tuner_roots[@]}")
  elif ((${#requests[@]})); then
    dvb_resolve_selection "${requests[@]}" || return
  else
    if ((${#dvb_selection_defaults[@]})); then
      # Resolve individually so an unplugged saved device does not make other
      # saved devices appear new or lose their checked state.
      for saved_request in "${dvb_selection_defaults[@]}"; do
        if dvb_resolve_selection "$saved_request"; then
          for i in "${dvb_selected_indices[@]}"; do saved[$i]=1; done
        else defaults_ok=false; fi
      done
    fi
    for i in "${!dvb_tuner_roots[@]}"; do
      labels[$i]=${dvb_tuner_labels[$i]}
      if [[ -n ${saved[$i]+x} ]]; then
        defaults+=("$i")
        labels[$i]+=' [Saved]'
      elif ((${#dvb_selection_defaults[@]})); then
        labels[$i]+=' [New]'
      fi
    done
    if [[ -t 0 || ${dvb_selection_ui:-text} == whiptail ]]; then
      if [[ ${dvb_selection_ui:-text} == whiptail ]] && command -v whiptail >/dev/null; then
        checklist=true
        for i in "${!dvb_tuner_roots[@]}"; do
          flag=OFF
          for n in "${defaults[@]}"; do [[ $n != "$i" ]] || flag=ON; done
          menu+=("$((i + 1))" "${labels[$i]} (adapters ${dvb_tuner_adapters[$i]})" "$flag")
        done
        reply=$(whiptail --title 'Select physical DVB devices' --separate-output --checklist \
          'Choose the device(s) to connect. All adapters in each selected device are included.' \
          20 100 10 "${menu[@]}" 3>&1 1>&2 2>&3) || return 2
      else
        printf '\nSelect physical DVB devices (all adapters of each device are included):\n' >&2
        for i in "${!dvb_tuner_roots[@]}"; do
          printf '  %s) %s — adapters %s\n     %s\n' "$((i + 1))" "${labels[$i]}" "${dvb_tuner_adapters[$i]}" "${dvb_tuner_ids[$i]:-${dvb_tuner_keys[$i]}}" >&2
        done
        printf 'Enter numbers separated by spaces, all, or q to cancel' >&2
        if ((${#defaults[@]})) && $defaults_ok; then printf ' (Enter keeps saved selection)' >&2; fi
        printf ': ' >&2
        read -r reply || return 2
      fi
      [[ $reply != q ]] || return 2
      if [[ -z $reply && ${#defaults[@]} -gt 0 ]] && $defaults_ok && ! $checklist; then
        dvb_selected_indices=("${defaults[@]}")
      elif [[ $reply == all ]]; then
        dvb_selected_indices=("${!dvb_tuner_roots[@]}")
      else
        reply=${reply//,/ }
        # Whiptail separate-output uses newlines; read all choices, not one line.
        reply=${reply//$'\n'/ }
        read -r -a choices <<<"$reply"
        requests=()
        for n in "${choices[@]}"; do
          [[ $n =~ ^[1-9][0-9]{0,5}$ ]] && ((10#$n <= ${#dvb_tuner_roots[@]})) || {
            dvb_selection_error "Invalid tuner number: $n"
            return 1
          }
          requests+=("${dvb_tuner_keys[$((10#$n - 1))]}")
        done
        dvb_resolve_selection "${requests[@]}" || return
      fi
    elif ((${#defaults[@]})) && $defaults_ok; then
      dvb_selected_indices=("${defaults[@]}")
    elif ((${#dvb_tuner_roots[@]} == 1)) && $defaults_ok; then
      dvb_selected_indices=(0)
    else
      dvb_selection_error 'Choose tuners explicitly (--tuner ID / --all-tuners, or var_dvb_tuners for community scripts); no usable saved selection'
      return 1
    fi
  fi
  ((${#dvb_selected_indices[@]})) || {
    dvb_selection_error 'Select at least one physical DVB device, or disable/remove DVB passthrough'
    return 1
  }
  dvb_selected_ids=()
  dvb_selected_nodes=()
  dvb_selected_roots=()
  for i in "${dvb_selected_indices[@]}"; do
    dvb_selected_ids+=("${dvb_tuner_ids[$i]:-${dvb_tuner_keys[$i]}}")
    dvb_selected_roots+=("${dvb_tuner_roots[$i]}")
    read -r -a choices <<<"${dvb_tuner_nodes[$i]}"
    dvb_selected_nodes+=("${choices[@]}")
  done
}

# Community UI/backends call this after sourcing this library. A selection
# supplied by advanced settings is reused without a second prompt.
dvb_community_select() {
  local value=${dvb_tuners:-${var_dvb_tuners:-}}
  local -a requests=()
  dvb_select_all=false
  dvb_selection_defaults=()
  dvb_discover_tuners || return
  ((${#dvb_tuner_roots[@]})) || {
    dvb_selection_error 'No initialized physical DVB devices found'
    return 1
  }
  if [[ $value == all ]]; then
    dvb_select_all=true
  elif [[ -n $value ]]; then
    value=${value//,/ }
    read -r -a requests <<<"$value"
  fi
  if [[ ${1:-} == --prompt ]]; then
    if $dvb_select_all; then
      for value in "${!dvb_tuner_roots[@]}"; do dvb_selection_defaults+=("${dvb_tuner_keys[$value]}"); done
    else dvb_selection_defaults=("${requests[@]}"); fi
    dvb_select_all=false
    requests=()
  fi
  dvb_select_tuners "${requests[@]}" || return
  dvb_tuners=$(
    IFS=,
    printf '%s' "${dvb_selected_ids[*]}"
  )
  var_dvb_tuners=$dvb_tuners
  export dvb_tuners var_dvb_tuners
}

dvb_community_pve_config() (
  local configuration=$1 root=${dvb_state_root:-${STABLE_ROOT:-/var/lib/dvb-passthrough}} target
  target=${CTID:-${configuration##*/}}
  target=${target%.conf}
  [[ $target =~ ^[1-9][0-9]*$ ]] || {
    dvb_selection_error 'Invalid Proxmox CTID'
    return 1
  }
  local STATE="$root/proxmox/$target"
  exec 9>"${dvb_operation_lock:-/run/lock/dvb-passthrough.lock}" || return
  flock -n 9 || {
    dvb_selection_error 'Another DVB operation is running'
    return 1
  }
  dvb_community_reselect || return
  dvb_assignment_preflight proxmox "$target" || return
  local dvb_community_state=$STATE
  dvb_community_pve_write "$configuration" || return
  dvb_power_update
)

dvb_community_reselect() {
  local -a requested=("${dvb_selected_ids[@]}")
  dvb_discover_tuners || return
  dvb_select_all=false
  dvb_select_tuners "${requested[@]}"
}

dvb_community_save_selection() {
  local directory=$1 temporary profile_app profile_user
  mkdir -p "$directory" && chmod 0700 "$directory" || return
  temporary=$(mktemp "$directory/.selection.XXXXXXXX") || return
  printf '%s\n' "${dvb_selected_ids[@]}" >"$temporary"
  chmod 0600 "$temporary" && mv -f -- "$temporary" "$directory/selection" || return
  case "${APP:-}" in
  Plex | plex)
    profile_app=plex
    profile_user=${dvb_service_user:-plex}
    ;;
  Tvheadend | TVHeadend | tvheadend)
    profile_app=tvheadend
    profile_user=${dvb_service_user:-hts}
    ;;
  *) return 0 ;;
  esac
  [[ $profile_user =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ ]] || return 1
  temporary=$(mktemp "$directory/.profile.XXXXXXXX") || return
  printf '%s\t%s\n' "$profile_app" "$profile_user" >"$temporary"
  chmod 0600 "$temporary" && mv -f -- "$temporary" "$directory/profile"
}

dvb_community_save_devices() {
  local directory=$1 filename=$2 temporary
  shift 2
  dvb_community_save_selection "$directory" || return
  temporary=$(mktemp "$directory/.devices.XXXXXXXX") || return
  printf '%s\n' "$@" >"$temporary"
  chmod 0600 "$temporary" && mv -f -- "$temporary" "$directory/$filename"
}

dvb_community_pve_write() {
  local configuration=$1 gid=${var_dvb_gid:-44} index=0 node clean
  local -a entries=()
  [[ $gid =~ ^(0|[1-9][0-9]{0,9})$ ]] && ((gid <= 2147483647)) || {
    dvb_selection_error 'Invalid container DVB GID'
    return 1
  }
  [[ -f $configuration ]] || return 1
  clean=$(cat "$configuration") || return
  dvb_check_pve_file "$configuration" 'existing target configuration' || return
  if grep -qE '^dev[0-9]+:.*(/dev/dvb/|/dev/media[0-9]+)|^lxc.mount.entry:.* /?dev/dvb' <<<"$clean"; then
    dvb_selection_error 'Existing DVB mappings require review before adding the selected devices'
    return 1
  fi
  for node in "${dvb_selected_nodes[@]}"; do
    [[ -c $node ]] || {
      dvb_selection_error "Selected device disappeared: $node"
      return 1
    }
    while grep -qE "^dev$index:" <<<"$clean"; do index=$((index + 1)); done
    ((index < 256)) || return 1
    entries+=("dev$index: path=$node,uid=0,gid=$gid,mode=0660")
    index=$((index + 1))
  done
  ((${#entries[@]})) || return 1
  if [[ -n ${dvb_community_state:-} ]]; then
    dvb_community_save_devices "$dvb_community_state" devices.pending "${entries[@]}" || return
  fi
  printf '\n%s\n' "${entries[@]}" >>"$configuration" || return
  if [[ -n ${dvb_community_state:-} ]]; then
    mv -f -- "$dvb_community_state/devices.pending" "$dvb_community_state/devices" || return
  fi
}

dvb_community_incus_config() (
  [[ $EUID == 0 ]] || {
    dvb_selection_error 'DVB host device management requires root; rerun as root or disable DVB'
    return 1
  }
  local target=$1 root=${dvb_state_root:-${STABLE_ROOT:-/var/lib/dvb-passthrough}} project=${INCUS_PROJECT:-${INCUS_PROJECT_NAME:-default}}
  [[ $project =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ && $target =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || return 1
  local STATE="$root/incus/$project/$target"
  [[ $project != default ]] || STATE="$root/$target"
  # Creation and the standalone utility serialize assignment changes together.
  exec 9>"${dvb_operation_lock:-/run/lock/dvb-passthrough.lock}" || return
  flock -n 9 || {
    dvb_selection_error 'Another DVB operation is running'
    return 1
  }
  dvb_community_reselect || return
  dvb_assignment_preflight incus "$target" || return
  local dvb_community_state=$STATE
  dvb_community_incus_write "$target" || return
  dvb_power_update
)

dvb_community_incus_write() {
  local target=$1 gid=${var_dvb_gid:-44} node key expanded sources source
  local -a keys=()
  [[ $gid =~ ^(0|[1-9][0-9]{0,9})$ ]] && ((gid <= 2147483647)) || {
    dvb_selection_error 'Invalid container DVB GID'
    return 1
  }
  ((${#dvb_selected_nodes[@]})) || {
    dvb_selection_error 'No selected DVB nodes'
    return 1
  }
  # Complete the host-side validation before the first API mutation.
  for node in "${dvb_selected_nodes[@]}"; do
    [[ -c $node ]] || {
      dvb_selection_error "Selected device disappeared: $node"
      return 1
    }
    key="dvbpt_${node#/dev/}"
    key=${key//\//_}
    keys+=("$key")
  done
  if [[ -n ${dvb_community_state:-} ]]; then
    expanded=$(incus config show "$target" --expanded) || return
    sources=$(dvb_incus_device_sources <<<"$expanded") || return
    while IFS= read -r source; do
      [[ -z $source ]] || dvb_check_source "$source" 'existing Incus target configuration' || return
    done <<<"$sources"
    for key in "${keys[@]}"; do
      if grep -qE "^  $key:" <<<"$expanded"; then
        dvb_selection_error "Existing Incus device $key requires review"
        return 1
      fi
    done
    dvb_community_save_devices "$dvb_community_state" devices "${keys[@]}" || return
  fi
  for node in "${dvb_selected_nodes[@]}"; do
    key="dvbpt_${node#/dev/}"
    key=${key//\//_}
    incus config device add "$target" "$key" unix-char "source=$node" "path=$node" \
      uid=0 "gid=$gid" mode=0660 required=false || return
  done
}
# END shared physical tuner selection

# Stable aliases deliberately live outside the kernel's adapter-number range.
# Proxmox follows a host symlink when reading its device number, then creates a
# private node at that same path inside the CT (with the configured UID/GID).
stable_inventory() {
  local cls name physical vid pid serial adapter
  local -A identities=()
  STABLE_INVENTORY=()
  for cls in "$SYS_ROOT"/class/dvb/*; do
    name=${cls##*/}
    [[ $name =~ ^dvb([0-9]+)\.frontend0$ && -e $cls/device ]] || continue
    adapter=${BASH_REMATCH[1]}
    [[ -c $DEV_ROOT/dvb/adapter$adapter/frontend0 ]] || continue
    physical=$(readlink -f "$cls/device") || return
    while [[ $physical != "$SYS_ROOT" && $physical != / ]]; do
      [[ ! -f $physical/idVendor || ! -f $physical/idProduct ]] || break
      physical=${physical%/*}
    done
    [[ -f $physical/serial ]] || continue
    vid=$(cat "$physical/idVendor"); pid=$(cat "$physical/idProduct"); serial=$(cat "$physical/serial")
    [[ $vid =~ ^[0-9a-f]{4}$ && $pid =~ ^[0-9a-f]{4}$ && $serial =~ ^[a-zA-Z0-9_.:-]+$ ]] || continue
    if [[ -n ${identities[$vid:$pid:$serial]+x} && ${identities[$vid:$pid:$serial]} != "$physical" ]]; then
      msg_error "Duplicate USB serial: $vid:$pid:$serial"; return 1
    fi
    identities[$vid:$pid:$serial]=$physical
    STABLE_INVENTORY+=("$vid $pid $serial $adapter")
  done
  if ((${#STABLE_INVENTORY[@]})); then
    mapfile -t STABLE_INVENTORY < <(printf '%s\n' "${STABLE_INVENTORY[@]}" | LC_ALL=C sort -k1,3 -k4,4n)
  fi
}

stable_validate_map() {
  local map=$1 slot vid pid serial channel count components extra component key
  local -A slots=() channels=() counts=()
  [[ -s $map ]] || { msg_error "Empty stable map: $map"; return 1; }
  while read -r slot vid pid serial channel count components extra; do
    [[ $slot =~ ^[1-9][0-9]{3,5}$ && $vid =~ ^[0-9a-f]{4}$ && $pid =~ ^[0-9a-f]{4}$ &&
       $serial =~ ^[a-zA-Z0-9_.:-]+$ && $channel =~ ^(0|[1-9][0-9]{0,2})$ && $count =~ ^[1-9][0-9]{0,2}$ &&
       $components =~ ^[a-zA-Z0-9_]+(,[a-zA-Z0-9_]+)*$ && -z $extra ]] || {
      msg_error "Invalid stable map: $map"; return 1;
    }
    ((10#$channel < 10#$count)) || return 1
    [[ -z ${slots[$slot]+x} && -z ${channels[$vid:$pid:$serial:$channel]+x} ]] || return 1
    key=$vid:$pid:$serial
    [[ -z ${counts[$key]+x} || ${counts[$key]} == "$count" ]] || return 1
    counts[$key]=$count; slots[$slot]=1; channels[$key:$channel]=1
  done < "$map"
  for key in "${!counts[@]}"; do
    for ((channel=0; channel<counts[$key]; channel++)); do
      [[ -n ${channels[$key:$channel]+x} ]] || { msg_error "Incomplete tuner group in $map"; return 1; }
    done
  done
}

stable_plan() {
  local record vid pid serial adapter key slot=1000 other line n component components dev saved_slot channel count chosen_slot
  local -a adapters=()
  local -A grouped=() used=() selected=() previous=() previous_counts=() previous_components=() counts=() seen=()
  stable_inventory || return
  STABLE_PLAN=()
  for dev in "${PASSTHROUGH_DEVICES[@]}"; do
    [[ $dev =~ ^/dev/dvb/adapter([0-9]+)/ ]] || { msg_error 'Stable mode currently supports USB DVB nodes only, not related media nodes'; return 1; }
    selected[${BASH_REMATCH[1]}]=1
  done
  if [[ -f $STATE/stable-map ]]; then
    stable_validate_map "$STATE/stable-map" || return
    while read -r saved_slot vid pid serial channel count components; do
      key="$vid $pid $serial"
      previous[$key:$channel]=$saved_slot; previous_counts[$key]=$count
      previous_components[$key:$channel]=$components
    done < "$STATE/stable-map"
  fi
  for other in "$STABLE_ROOT"/proxmox/*/stable-map; do
    [[ -f $other ]] || continue
    stable_validate_map "$other" || return
    while read -r n line; do used[$n]=1; done < "$other"
  done
  for record in "${STABLE_INVENTORY[@]}"; do
    read -r vid pid serial adapter <<< "$record"
    key="$vid $pid $serial"
    counts[$key]=$((${counts[$key]:-0} + 1))
    [[ -n ${selected[$adapter]+x} ]] || continue
    grouped[$key]="${grouped[$key]:-} $adapter"
    selected[$adapter]=found
  done
  for adapter in "${!selected[@]}"; do
    [[ ${selected[$adapter]} == found ]] || { msg_error "Adapter $adapter has no unique usable USB serial"; return 1; }
  done
  for record in "${STABLE_INVENTORY[@]}"; do
    read -r vid pid serial adapter <<< "$record"
    key="$vid $pid $serial"
    [[ -n ${grouped[$key]+x} && -z ${seen[$key]+x} ]] || continue
    seen[$key]=1
    read -r -a adapters <<< "${grouped[$key]}"
    [[ ${#adapters[@]} == "${counts[$key]}" ]] || { msg_error "Select every adapter of physical tuner $serial"; return 1; }
    [[ -z ${previous_counts[$key]+x} || ${previous_counts[$key]} == "${#adapters[@]}" ]] || { msg_error "Incomplete or changed adapter group for $serial"; return 1; }
    for n in "${!adapters[@]}"; do
      adapter=${adapters[$n]}; components=''
      for dev in "${PASSTHROUGH_DEVICES[@]}"; do
        [[ $dev == /dev/dvb/adapter$adapter/* ]] || continue
        component=${dev##*/}; components+="${components:+,}$component"
      done
      [[ -z ${previous_components[$key:$n]+x} || ${previous_components[$key:$n]} == "$components" ]] || { msg_error "Incomplete or changed component set for $serial"; return 1; }
      if [[ -n ${previous[$key:$n]+x} ]]; then chosen_slot=${previous[$key:$n]}
      else
        while [[ -n ${used[$slot]+x} ]]; do slot=$((slot + 1)); done
        chosen_slot=$slot; used[$slot]=1; slot=$((slot + 1))
      fi
      STABLE_PLAN+=("$chosen_slot $vid $pid $serial $n ${#adapters[@]} $components")
    done
  done
  ((${#STABLE_PLAN[@]})) || { msg_error 'No serial-identified tuners found'; return 1; }
  # Resolve in memory first: dry runs must not create host aliases or state.
  stable_resolve "$(printf '%s\n' "${STABLE_PLAN[@]}")" || return
  PASSTHROUGH_DEVICES=("${STABLE_ALIASES[@]}")
}

stable_prune_previous() {
  local previous=$1 line slot temporary
  local -a removed=()
  stable_validate_map "$previous" || return
  while IFS= read -r line; do
    slot=${line%% *}
    grep -q "^$slot " "$STATE/stable-map" || removed+=("$line")
  done < "$previous"
  if ((${#removed[@]})); then
    temporary=$(mktemp "$STATE/.removed-map.XXXXXXXX") || return
    printf '%s\n' "${removed[@]}" > "$temporary"
    (exec 8>/run/lock/dvb-passthrough-stable.lock; flock 8; stable_clear_map "$temporary") || return
    rm -- "$temporary"
  fi
}

stable_resolve() {
  local plan=$1 slot vid pid serial channel count components record v p s adapter component cls source alias actual
  local -a adapters=() parts=()
  STABLE_ALIASES=(); STABLE_SOURCES=()
  while read -r slot vid pid serial channel count components; do
    adapters=()
    for record in "${STABLE_INVENTORY[@]}"; do
      read -r v p s adapter <<< "$record"
      [[ $v != "$vid" || $p != "$pid" || $s != "$serial" ]] || adapters+=("$adapter")
    done
    if ((${#adapters[@]} != count)); then
      msg_error "Serial $serial: expected $count adapters, found ${#adapters[@]}"; return 1
    fi
    adapter=${adapters[$channel]}
    IFS=',' read -r -a parts <<< "$components"
    for component in "${parts[@]}"; do
      source="$DEV_ROOT/dvb/adapter$adapter/$component"
      alias="$DEV_ROOT/dvb/adapter$slot/$component"
      cls="$SYS_ROOT/class/dvb/dvb$adapter.$component"
      [[ -c $source && -f $cls/dev && -e $cls/device ]] || { msg_error "Missing registered node: $source"; return 1; }
      # A leftover character file alone does not prove the driver still owns it.
      actual=$(stat -Lc '%t:%T' "$source") || return
      printf -v actual '%d:%d' "0x${actual%:*}" "0x${actual#*:}"
      [[ $(cat "$cls/dev") == "$actual" ]] || { msg_error "Stale host node: $source"; return 1; }
      [[ ! -e $SYS_ROOT/class/dvb/dvb$slot.$component ]] || { msg_error "Alias conflicts with kernel adapter $slot"; return 1; }
      [[ ! -e $alias && ! -L $alias || -L $alias ]] || { msg_error "Refusing to replace non-symlink $alias"; return 1; }
      STABLE_ALIASES+=("$alias"); STABLE_SOURCES+=("$source")
    done
  done <<< "$plan"
}

stable_clear_map() {
  local map=$1 slot vid pid serial channel count components component alias
  local -a parts=()
  stable_validate_map "$map" || return
  while read -r slot vid pid serial channel count components; do
    IFS=',' read -r -a parts <<< "$components"
    for component in "${parts[@]}"; do
      alias="$DEV_ROOT/dvb/adapter$slot/$component"
      [[ ! -e $SYS_ROOT/class/dvb/dvb$slot.$component ]] || continue
      [[ ! -L $alias ]] || rm -- "$alias" || return
    done
    # Only remove empty alias directories, never device nodes or other files.
    rmdir "$DEV_ROOT/dvb/adapter$slot" 2>/dev/null || true
  done < "$map"
}

stable_refresh_all() {
  local map i temporary failed=false
  if ! stable_inventory; then
    for map in "$STABLE_ROOT"/proxmox/*/stable-map; do
      [[ ! -f $map ]] || stable_clear_map "$map" || return
    done
    return 1
  fi
  for map in "$STABLE_ROOT"/proxmox/*/stable-map; do
    [[ -f $map ]] || continue
    stable_validate_map "$map" || return
    if ! stable_resolve "$(cat "$map")"; then
      stable_clear_map "$map" || return
      failed=true
      continue
    fi
    for i in "${!STABLE_ALIASES[@]}"; do
      mkdir -p "${STABLE_ALIASES[$i]%/*}" || return
      chmod 0755 "${STABLE_ALIASES[$i]%/*}" || return
      temporary="${STABLE_ALIASES[$i]}.dvbpt-$$"
      ln -s -- "${STABLE_SOURCES[$i]}" "$temporary" || return
      mv -Tf -- "$temporary" "${STABLE_ALIASES[$i]}" || return
    done
  done
  ! $failed
}

# BEGIN shared DVB banner helpers
dvb_banner_profile() {
  cat <<'PROFILE'
#!/bin/sh
# Managed by dvb-passthrough.sh
# Availability only: inspect registrations, never open a tuner at login.
case $- in
*i*)
if [ -t 1 ]; then
  (
    sys=/sys
    dev=/dev
    manifest=/var/lib/dvb-passthrough/banner-devices.tsv
    [ -r "$manifest" ] || exit 0
    summary=''
    tab=$(printf '\t')
    while IFS="$tab" read -r identity label nodes extra; do
      case "$identity" in usb:*|path:*) ;; *) continue ;; esac
      [ -n "$label" ] && [ -n "$nodes" ] && [ -z "$extra" ] || continue
      label=$(printf '%s' "$label" | LC_ALL=C tr -cd '[:print:]')
      total=0; available=0; seen=''
      previous_ifs=$IFS
      IFS=,
      for node in $nodes; do
        case "$node" in /dev/dvb/adapter*/frontend*) ;; *) continue ;; esac
        rest=${node#/dev/dvb/adapter}; adapter=${rest%%/*}; frontend=${rest#*/frontend}
        case "$adapter:$frontend" in *[!0-9:]*|:*|*:) continue ;; esac
        total=$((total+1))
        node="$dev/${node#/dev/}"
        [ -c "$node" ] || continue
        number=$(stat -Lc '%t:%T' "$node" 2>/dev/null) || continue
        number=$(printf '%d:%d' "0x${number%:*}" "0x${number#*:}") || continue
        case " $seen " in *" $number "*) continue ;; esac
        seen="$seen $number"
        registration=$(readlink -f "$sys/dev/char/$number") || continue
        printf '%s\n' "${registration##*/}" | grep -Eq '^dvb[0-9]+\.frontend[0-9]+$' || continue
        physical=$(readlink -f "$registration/device") || continue
        [ -d "$physical" ] || continue
        while [ "$physical" != "$sys" ] && [ "$physical" != / ] && [ -n "$physical" ]; do
          if [ -f "$physical/idVendor" ] && [ -f "$physical/idProduct" ]; then break; fi
          if [ -L "$physical/subsystem" ] && [ "$(readlink -f "$physical/subsystem")" = "$sys/bus/pci" ]; then break; fi
          physical=${physical%/*}
        done
        case "$identity" in
          usb:*)
            [ -r "$physical/serial" ] && [ -r "$physical/idVendor" ] && [ -r "$physical/idProduct" ] || continue
            actual="usb:$(cat "$physical/idVendor"):$(cat "$physical/idProduct"):$(cat "$physical/serial")"
            ;;
          path:*) actual="path:${physical#"$sys"/devices/}" ;;
        esac
        [ "$actual" = "$identity" ] || continue
        available=$((available+1))
      done
      IFS=$previous_ifs
      [ "$total" -gt 0 ] || continue
      plural=''; [ "$total" -eq 1 ] || plural=s
      if [ "$available" -eq "$total" ]; then
        detail="$total tuner$plural"
      elif [ "$available" -eq 0 ]; then
        detail="missing; $total configured tuner$plural"
      else
        detail="$available/$total tuners available"
      fi
      summary="$summary${summary:+, }$label ($detail)"
    done < "$manifest"
    [ -z "$summary" ] || printf '  📺  \033[m\033[33mDVB Devices: \033[1;92m%s\033[m\n' "$summary"
  ) 2>/dev/null || :
fi
;;
esac
PROFILE
}

dvb_banner_validate() {
  local file=$1 identity label nodes extra node
  local -a parts=()
  local -A identities=() paths=()
  grep -qxF '# Managed by dvb-passthrough.sh' "$file" || return 1
  while IFS=$'\t' read -r identity label nodes extra; do
    [[ $identity != '# Managed by dvb-passthrough.sh' ]] || continue
    [[ $identity =~ ^(usb:|path:)[a-zA-Z0-9_.:/-]+$ && -n $label && ! $label =~ [[:cntrl:]] &&
      $nodes =~ ^/dev/dvb/adapter[0-9]+/frontend[0-9]+(,/dev/dvb/adapter[0-9]+/frontend[0-9]+)*$ && -z $extra ]] || return 1
    [[ -z ${identities[$identity]+x} ]] || return 1
    identities[$identity]=1
    IFS=, read -r -a parts <<<"$nodes"
    for node in "${parts[@]}"; do
      [[ -z ${paths[$node]+x} ]] || return 1
      paths[$node]=1
    done
  done <"$file"
}

# Initial community installation derives its roster only from assigned nodes.
# Skip kernel-name directory aliases and deduplicate character device numbers.
dvb_banner_capture() {
  local sys=${SYS_ROOT:-/sys} dev=${DEV_ROOT:-/dev} node number registration physical label key
  local -a roots=()
  local -A seen=() labels=() nodes=()
  for node in "$dev"/dvb/adapter*/frontend*; do
    [[ -c $node && ! -L ${node%/*} && $node =~ /adapter[0-9]+/frontend[0-9]+$ ]] || continue
    number=$(stat -Lc '%t:%T' "$node") || return
    [[ -z ${seen[$number]+x} ]] || continue
    seen[$number]=1
    printf -v number '%d:%d' "0x${number%:*}" "0x${number#*:}"
    registration=$(readlink -f "$sys/dev/char/$number") || continue
    [[ ${registration##*/} =~ ^dvb[0-9]+\.frontend[0-9]+$ ]] || continue
    physical=$(readlink -f "$registration/device") || continue
    [[ -d $physical ]] || continue
    while [[ $physical != "$sys" && $physical != / ]]; do
      [[ ! -f $physical/idVendor || ! -f $physical/idProduct ]] || break
      if [[ -L $physical/subsystem && $(readlink -f "$physical/subsystem") == "$sys/bus/pci" ]]; then break; fi
      physical=${physical%/*}
    done
    [[ $physical != "$sys" && $physical != / ]] || continue
    if [[ -z ${nodes[$physical]+x} ]]; then
      roots+=("$physical")
      label="PCI ${physical##*/}"
      if [[ -f $physical/product ]]; then
        label=$(cat "$physical/product")
      elif [[ -f $physical/idVendor && -f $physical/idProduct ]]; then label="USB $(cat "$physical/idVendor"):$(cat "$physical/idProduct")"; fi
      label=$(printf '%s' "$label" | LC_ALL=C tr -cd '[:print:]')
      labels[$physical]=${label:-DVB device}
      nodes[$physical]=''
    fi
    nodes[$physical]+="${nodes[$physical]:+,}/dev/${node#"$dev"/}"
  done
  printf '%s\n' '# Managed by dvb-passthrough.sh'
  for key in "${roots[@]}"; do
    printf 'path:%s\t%s\t%s\n' "${key#"$sys"/devices/}" "${labels[$key]}" "${nodes[$key]}"
  done
}

# shellcheck disable=SC2120 # The standalone transport passes --manifest-stdin.
dvb_banner_install() {
  local state=${dvb_banner_state:-/var/lib/dvb-passthrough}
  local profile=${dvb_banner_profile:-/etc/profile.d/10-dvb-details.sh}
  local backup_root=${dvb_banner_backup_root:-/var/backups/dvb-passthrough}
  local file manifest temporary roster backup
  manifest="$state/banner-devices.tsv"
  for file in "$profile" "$manifest"; do
    [[ ! -e $file && ! -L $file ]] || {
      [[ -f $file && ! -L $file ]] && grep -qxF '# Managed by dvb-passthrough.sh' "$file" || {
        printf 'Unrecognized DVB banner file: %s\n' "$file" >&2
        return 1
      }
    }
  done
  mkdir -p "$state" "${profile%/*}" "$backup_root" || return
  chmod 0755 "$state" || return
  chmod 0700 "$backup_root" || return
  roster=$(mktemp "$state/.banner-roster.XXXXXXXX") || return
  if [[ ${1:-} == --manifest-stdin ]]; then
    cat >"$roster" || return
  elif [[ -f $manifest ]]; then
    cp -- "$manifest" "$roster" || return
  else
    dvb_banner_capture >"$roster" || return
  fi
  if ! dvb_banner_validate "$roster"; then
    rm -- "$roster"
    printf 'Invalid DVB banner roster\n' >&2
    return 1
  fi
  backup=$(mktemp -d "$backup_root/banner.XXXXXXXX") || return
  chmod 0700 "$backup" || return
  for file in "$profile" "$manifest"; do
    [[ ! -f $file ]] || cp -- "$file" "$backup/" || return
  done
  temporary=$(mktemp "${profile}.XXXXXXXX") || return
  dvb_banner_profile >"$temporary" || return
  chmod 0755 "$temporary" || return
  chmod 0644 "$roster" || return
  mv -Tf -- "$roster" "$manifest" || return
  mv -Tf -- "$temporary" "$profile" || return
  printf 'DVB banner installed. Backup: %s\n' "$backup"
}

dvb_banner_library() {
  printf '%s\n' '#!/usr/bin/env bash' '# Managed by dvb-passthrough.sh'
  declare -f dvb_banner_profile dvb_banner_validate dvb_banner_capture dvb_banner_install
}
# END shared DVB banner helpers

dvb_banner_host_manifest() {
  local i j root label nodes node mapped position=0
  printf '%s\n' '# Managed by dvb-passthrough.sh'
  for i in "${dvb_selected_indices[@]}"; do
    root=${dvb_tuner_roots[$i]}; nodes=''
    label="PCI ${root##*/}"
    if [[ -f $root/product ]]; then label=$(cat "$root/product")
    elif [[ -f $root/idVendor && -f $root/idProduct ]]; then label="USB $(cat "$root/idVendor"):$(cat "$root/idProduct")"; fi
    label=$(printf '%s' "$label" | LC_ALL=C tr -cd '[:print:]')
    local -a devices=()
    read -r -a devices <<< "${dvb_tuner_nodes[$i]}"
    for node in "${devices[@]}"; do
      [[ $node =~ ^/dev/dvb/adapter[0-9]+/frontend[0-9]+$ ]] || continue
      mapped=$node
      if ${STABLE:-false}; then
        mapped=''
        for j in "${!STABLE_SOURCES[@]}"; do
          [[ ${STABLE_SOURCES[$j]} != "$node" ]] || { mapped=${STABLE_ALIASES[$j]}; break; }
        done
        [[ -n $mapped ]] || { msg_error "No stable banner mapping for $node"; return 1; }
      fi
      nodes+="${nodes:+,}$mapped"
    done
    [[ -n $nodes ]] || { msg_error "No frontend nodes for banner device ${dvb_selected_ids[$position]}"; return 1; }
    printf '%s\t%s\t%s\n' "${dvb_selected_ids[$position]}" "${label:-DVB device}" "$nodes"
    position=$((position+1))
  done
}

dvb_install_container_banner() {
  local manifest
  if ! $CT_RUNNING; then
    msg_warn 'Container stopped: rerun after starting it to install the DVB login banner.'
    return 0
  fi
  manifest=$(dvb_banner_host_manifest) || return
  run_in_ct sh -c '
    set -eu
    umask 077
    mkdir -p /usr/local/lib/community-scripts /var/backups/dvb-passthrough
    file=/usr/local/lib/community-scripts/dvb-banner.func
    if [ -e "$file" ] || [ -L "$file" ]; then
      [ -f "$file" ] && [ ! -L "$file" ] && grep -qxF "# Managed by dvb-passthrough.sh" "$file" || exit 1
      backup=$(mktemp -d /var/backups/dvb-passthrough/banner-module.XXXXXXXX)
      cp "$file" "$backup/"
    fi
    temporary=$(mktemp /usr/local/lib/community-scripts/.dvb-banner.XXXXXXXX)
    cat > "$temporary"
    chmod 0644 "$temporary"
    mv -Tf "$temporary" "$file"
  ' < <(dvb_banner_library)
  run_in_ct bash -c 'source /usr/local/lib/community-scripts/dvb-banner.func; dvb_banner_install --manifest-stdin' <<< "$manifest"
}

dvb_remove_container_banner() {
  if ! $CT_RUNNING; then
    msg_warn 'Container stopped: remove the managed DVB container helper files after starting it (see README).'
    return 0
  fi
  run_in_ct sh -c '
    set -eu
    for file in /etc/profile.d/10-dvb-details.sh /var/lib/dvb-passthrough/banner-devices.tsv /usr/local/lib/community-scripts/dvb-banner.func /usr/local/lib/community-scripts/dvb.func; do
      if [ -e "$file" ] || [ -L "$file" ]; then
        [ -f "$file" ] && [ ! -L "$file" ] || exit 1
        if [ "$file" = /usr/local/lib/community-scripts/dvb.func ]; then
          grep -qxF -e "# Managed by dvb-passthrough.sh" -e "# Container permissions only. Physical selection belongs to the host backend." "$file" || exit 1
        else
          grep -qxF "# Managed by dvb-passthrough.sh" "$file" || exit 1
        fi
      fi
    done
    umask 077
    mkdir -p /var/backups/dvb-passthrough
    backup=$(mktemp -d /var/backups/dvb-passthrough/banner-removal.XXXXXXXX)
    for file in /etc/profile.d/10-dvb-details.sh /var/lib/dvb-passthrough/banner-devices.tsv /usr/local/lib/community-scripts/dvb-banner.func /usr/local/lib/community-scripts/dvb.func; do
      if [ -f "$file" ]; then cp "$file" "$backup/"; rm -- "$file"; fi
    done
  '
}

stable_guest_helper() {
  printf '%s\n' '#!/usr/bin/env bash' '# Managed by dvb-passthrough.sh'
  case "${APPLICATION:-}" in
    tvheadend) printf '%s\n' 'dvb_discovery_mode=tvheadend' ;;
    *) printf '%s\n' 'dvb_discovery_mode=generic' ;;
  esac
  cat <<'GUEST'
set -euo pipefail
SYS_ROOT=/sys
DEV_ROOT=/dev
shopt -s nullglob
count=0
for frontend in "$DEV_ROOT"/dvb/adapter*/frontend0; do
  [[ $frontend =~ /adapter([1-9][0-9]{3,5})/frontend0$ && -c $frontend ]] || continue
  slot=${BASH_REMATCH[1]}
  number=$(stat -Lc '%t:%T' "$frontend")
  printf -v number '%d:%d' "0x${number%:*}" "0x${number#*:}"
  found=false
  for cls in "$SYS_ROOT"/class/dvb/dvb*.frontend0; do
    [[ -f $cls/dev && $(cat "$cls/dev") == "$number" ]] || continue
    name=${cls##*/}
    [[ $name =~ ^dvb([0-9]+)\.frontend0$ ]] || exit 1
    actual=${BASH_REMATCH[1]}
    path="$DEV_ROOT/dvb/adapter$actual"
    target="adapter$slot"
    # Udev-based applications discover the kernel DEVNAME, then open that path.
    # Link it to the private Proxmox node without exposing any extra hardware.
    if [[ $dvb_discovery_mode == tvheadend ]]; then
      # TVHeadend discovers the private stable directories directly. Original
      # kernel aliases would duplicate each frontend in its adapter list.
      if [[ -L $path ]]; then
        [[ $(readlink "$path") == "$target" ]] || { echo "Conflicting DVB alias: $path" >&2; exit 1; }
        rm -- "$path"
      elif [[ -e $path ]]; then
        echo "Unmanaged kernel DVB path conflicts with TVHeadend stable discovery: $path" >&2
        exit 1
      fi
    else
      if [[ -L $path ]]; then
        [[ $(readlink "$path") == "$target" ]] || { echo "Conflicting DVB alias: $path" >&2; exit 1; }
      elif [[ -e $path ]]; then
        [[ -c $path/frontend0 && $(stat -Lc '%t:%T' "$path/frontend0") == "$(stat -Lc '%t:%T' "$frontend")" ]] || exit 1
      else
        ln -s -- "$target" "$path"
      fi
    fi
    found=true; count=$((count + 1)); break
  done
  $found || { echo "No kernel registration for $frontend" >&2; exit 1; }
done
printf 'DVB application discovery paths verified: %s\n' "$count"
GUEST
}

stable_install_guest() {
  # /dev is recreated on every CT boot. Install the compatibility links before
  # media services start, matching current kernel numbers by major/minor only.
  run_in_ct sh -c '
    set -eu
    umask 077
    mkdir -p /var/backups/dvb-passthrough /usr/local/sbin
    backup=$(mktemp -d /var/backups/dvb-passthrough/stable.XXXXXXXX)
    for f in /usr/local/sbin/dvb-passthrough-aliases /etc/systemd/system/dvb-passthrough-aliases.service; do
      if [ -f "$f" ]; then
        grep -q "^# Managed by dvb-passthrough.sh$" "$f" || exit 1
        cp "$f" "$backup/"
      fi
    done
    cat > /usr/local/sbin/dvb-passthrough-aliases
    chmod 0755 /usr/local/sbin/dvb-passthrough-aliases
    printf "Container helper backup: %s\n" "$backup"
  ' < <(stable_guest_helper)
  run_in_ct sh -c 'cat > /etc/systemd/system/dvb-passthrough-aliases.service' <<'UNIT'
# Managed by dvb-passthrough.sh
[Unit]
Description=Provide DVB kernel discovery paths for stable passthrough
Before=plexmediaserver.service jellyfin.service tvheadend.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/dvb-passthrough-aliases
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
UNIT
  run_in_ct systemctl daemon-reload
  run_in_ct systemctl enable dvb-passthrough-aliases.service
}

stable_install() {
  local file temporary
  mkdir -p /usr/local/libexec /etc/systemd/system/pve-guests.service.d
  for file in /usr/local/libexec/dvb-passthrough /etc/udev/rules.d/98-dvb-passthrough-stable.rules /etc/systemd/system/dvb-passthrough-stable.service /etc/systemd/system/pve-guests.service.d/dvb-passthrough-stable.conf; do
    [[ ! -f $file ]] || cp -- "$file" "$HOST_BACKUP/${file##*/}"
    if [[ -f $file && $file != /usr/local/libexec/dvb-passthrough ]]; then
      grep -q '^# Managed by dvb-passthrough.sh$' "$file" || { msg_error "Unrecognized stable-mapping file: $file"; return 1; }
    fi
  done
  # Keep an installed copy, independent of where the operator runs this file.
  temporary=$(mktemp /usr/local/libexec/.dvb-passthrough.XXXXXXXX)
  cp -- "${BASH_SOURCE[0]}" "$temporary"
  chmod 0755 "$temporary"
  mv -T -- "$temporary" /usr/local/libexec/dvb-passthrough
  cat > /etc/udev/rules.d/98-dvb-passthrough-stable.rules <<'RULE'
# Managed by dvb-passthrough.sh
ACTION=="add|change|remove", SUBSYSTEM=="dvb", RUN+="/usr/local/libexec/dvb-passthrough --refresh-stable"
RULE
  cat > /etc/systemd/system/dvb-passthrough-stable.service <<'UNIT'
# Managed by dvb-passthrough.sh
[Unit]
Description=Refresh serial-based DVB passthrough aliases
After=systemd-udev-trigger.service
Before=pve-guests.service
[Service]
Type=oneshot
ExecStart=/usr/local/libexec/dvb-passthrough --refresh-stable --wait
TimeoutStartSec=75
UNIT
  cat > /etc/systemd/system/pve-guests.service.d/dvb-passthrough-stable.conf <<'UNIT'
# Managed by dvb-passthrough.sh
[Unit]
Wants=dvb-passthrough-stable.service
After=dvb-passthrough-stable.service
UNIT
  write_tracking "$STATE/stable-map" "${STABLE_PLAN[@]}"
  chmod 0600 "$STATE/stable-map"
  systemctl daemon-reload
  udevadm control --reload-rules
  # Share the event-helper lock to avoid racing a concurrent udev refresh.
  (exec 8>/run/lock/dvb-passthrough-stable.lock; flock 8; stable_refresh_all)
  stable_install_guest
}

dvb_resolve_profile() {
  local saved_app='' saved_user='' extra=''
  dvb_default_account=false
  if [[ -f $STATE/profile ]]; then
    IFS=$'\t' read -r saved_app saved_user extra < "$STATE/profile" || return
    [[ -z $extra && ( $saved_app == plex || $saved_app == tvheadend || $saved_app == custom ) &&
       $saved_user =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ && $(wc -l < "$STATE/profile") == 1 ]] || {
      msg_error 'Invalid saved application profile'; return 1;
    }
  fi
  if [[ -z ${APPLICATION:-} ]]; then
    APPLICATION=${saved_app:-plex}
    if [[ -z ${SERVICE_USER:-} ]]; then SERVICE_USER=$saved_user; fi
  fi
  if [[ -z ${SERVICE_USER:-} ]]; then
    dvb_default_account=true
    case "$APPLICATION" in
      plex) SERVICE_USER=plex ;;
      tvheadend) SERVICE_USER=hts ;;
      *) msg_error 'Custom profile requires --user'; return 1 ;;
    esac
  fi
  [[ $SERVICE_USER =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ ]] || { msg_error 'Invalid service user'; return 1; }
}

# BEGIN shared container permissions
setup_dvb() {
  local service_user=${1:-} node gid group
  local -A seen=()
  export dvb_groups_changed=0
  [[ -d /dev/dvb ]] || return 0
  [[ -n $service_user ]] && id "$service_user" >/dev/null 2>&1 || return 1
  for node in /dev/dvb/adapter*/* /dev/media*; do
    [[ -c $node ]] || continue
    if runuser -u "$service_user" -- test -r "$node" && runuser -u "$service_user" -- test -w "$node"; then
      continue
    fi
    gid=$(stat -Lc '%g' "$node") || return
    [[ $gid != 0 ]] || {
      printf 'Refusing to grant root-group membership for %s\n' "$node" >&2
      return 1
    }
    if [[ -z ${seen[$gid]+x} ]]; then
      seen[$gid]=1
      group=$(getent group "$gid") || group=''
      group=${group%%:*}
      if [[ -z $group ]]; then
        group="dvb_$gid"
        groupadd -g "$gid" "$group" || return
      fi
      if [[ " $(id -G "$service_user") " != *" $gid "* ]]; then
        usermod -aG "$group" "$service_user" || return
        dvb_groups_changed=1
      fi
    fi
    runuser -u "$service_user" -- test -r "$node" || return
    runuser -u "$service_user" -- test -w "$node" || return
  done
}

# Prefer the actual daemon identity, including accounts configured through a
# systemd override. Parse defaults as data only when the daemon is not running.
dvb_tvheadend_user() {
  local user=${dvb_service_user:-} pid uid record options configured
  if [[ -z $user ]]; then
    pid=$(systemctl show tvheadend.service -p MainPID --value) || return
    if [[ $pid =~ ^[1-9][0-9]*$ ]]; then
      uid=$(awk '/^Uid:/ {print $3}' "${dvb_proc_root:-/proc}/$pid/status") || return
      [[ $uid != 0 ]] || {
        printf 'TVHeadend is running as root; configure its service account first\n' >&2
        return 1
      }
      record=$(getent passwd "$uid") || return
      user=${record%%:*}
    else
      user=$(systemctl show tvheadend.service -p User --value) || return
      if [[ -z $user || $user == root ]]; then
        user=hts
        if [[ -r ${dvb_tvh_defaults:-/etc/default/tvheadend} ]]; then
          options=$(awk '/^OPTIONS=/ {line=$0} END {print line}' "${dvb_tvh_defaults:-/etc/default/tvheadend}") || return
          options=${options//\"/ }
          options=${options//\'/ }
          configured='(^|[[:space:]])-u[[:space:]]+([a-zA-Z_][a-zA-Z0-9_-]*\$?)([[:space:]]|$)'
          if [[ $options =~ $configured ]]; then user=${BASH_REMATCH[2]}; fi
        fi
      fi
    fi
  fi
  [[ $user =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ ]] && id "$user" >/dev/null 2>&1 || return 1
  printf '%s\n' "$user"
}

dvb_verify_service() {
  local user=$1 service=$2 pid uid expected_uid groups gid node
  pid=$(systemctl show "$service" -p MainPID --value) || return
  [[ $pid =~ ^[1-9][0-9]*$ ]] || {
    printf '%s is not running; device verification incomplete\n' "$service" >&2
    return 1
  }
  uid=$(awk '/^Uid:/ {print $3}' "${dvb_proc_root:-/proc}/$pid/status") || return
  expected_uid=$(id -u "$user") || return
  [[ $uid == "$expected_uid" ]] || {
    printf '%s is running as UID %s, expected %s\n' "$service" "$uid" "$expected_uid" >&2
    return 1
  }
  groups=$(awk '/^Gid:/ {print $3} /^Groups:/ {for (i=2;i<=NF;i++) print $i}' "${dvb_proc_root:-/proc}/$pid/status") || return
  for node in /dev/dvb/adapter*/* /dev/media*; do
    [[ -c $node ]] || continue
    runuser -u "$user" -- test -r "$node" && runuser -u "$user" -- test -w "$node" || return 1
    gid=$(stat -Lc '%g' "$node") || return
    if ! grep -qxF "$gid" <<<"$groups"; then
      printf '%s has not acquired device GID %s for %s; restart the service\n' "$service" "$gid" "$node" >&2
      return 1
    fi
  done
  if [[ $service == tvheadend.service ]]; then
    local number
    local -A seen_frontends=()
    for node in /dev/dvb/adapter*/frontend*; do
      [[ -c $node ]] || continue
      number=$(stat -Lc '%t:%T' "$node") || return
      if [[ -n ${seen_frontends[$number]+x} ]]; then
        printf 'Duplicate TVHeadend discovery paths: %s and %s; restart the container after stable setup or review unmanaged aliases\n' "${seen_frontends[$number]}" "$node" >&2
        return 1
      fi
      seen_frontends[$number]=$node
    done
  fi
}

dvb_verify_roster() {
  local manifest=${dvb_banner_state:-/var/lib/dvb-passthrough}/banner-devices.tsv id _label paths node
  local -a nodes=()
  [[ -f $manifest ]] || return 0
  grep -qxF '# Managed by dvb-passthrough.sh' "$manifest" || return 1
  while IFS=$'\t' read -r id _label paths; do
    [[ $id != \#* && -n $id ]] || continue
    IFS=',' read -r -a nodes <<<"$paths"
    for node in "${nodes[@]}"; do
      [[ $node == /dev/dvb/adapter*/frontend* ]] || return 1
      [[ -c $node ]] || {
        printf 'Configured DVB frontend missing: %s\n' "$node" >&2
        return 1
      }
    done
  done <"$manifest"
}

dvb_permissions_library() {
  printf '%s\n' '#!/usr/bin/env bash' '# Managed by dvb-passthrough.sh'
  declare -f setup_dvb dvb_tvheadend_user dvb_verify_service dvb_verify_roster
}

dvb_require_nodes() {
  local node
  [[ ${ENABLE_DVB:-${var_dvb:-no}} == yes ]] || return 0
  for node in /dev/dvb/adapter*/*; do [[ ! -c $node ]] || return 0; done
  printf 'DVB passthrough was enabled, but no assigned character devices are visible\n' >&2
  return 1
}

dvb_store_library() {
  local destination=$1 content=$2 backup temporary
  if [[ -e $destination || -L $destination ]]; then
    [[ -f $destination && ! -L $destination ]] &&
      grep -qxF -e '# Managed by dvb-passthrough.sh' -e '# Container permissions only. Physical selection belongs to the host backend.' "$destination" || {
      printf 'Unrecognized DVB helper: %s\n' "$destination" >&2
      return 1
    }
    backup=$(mktemp -d "${dvb_helper_backup_root:-/var/backups/dvb-passthrough}/helpers.XXXXXXXX") || return
    cp -p -- "$destination" "$backup/" || return
  fi
  temporary=$(mktemp "${destination}.XXXXXXXX") || return
  printf '%s\n' "$content" >"$temporary"
  chmod 0644 "$temporary" && mv -f -- "$temporary" "$destination" || {
    rm -f -- "$temporary"
    return 1
  }
}

dvb_install_helpers() {
  local permissions_source=${1:-} banner_source directory=${dvb_helper_dir:-/usr/local/lib/community-scripts}
  [[ -n $permissions_source ]] || permissions_source=$(dvb_permissions_library) || return
  banner_source=$(dvb_banner_library) || return
  mkdir -p "$directory" "${dvb_helper_backup_root:-/var/backups/dvb-passthrough}" || return
  dvb_store_library "$directory/dvb.func" "$permissions_source" || return
  dvb_store_library "$directory/dvb-banner.func" "$banner_source" || return
  dvb_banner_install
}

dvb_prepare_container() {
  local user=$1 source=${2:-}
  setup_dvb "$user" || return
  dvb_install_helpers "$source"
}
# END shared container permissions

DRY_RUN=false
ASSUME_YES=false
VERBOSE=false
REMOVE=false
STABLE=false
REFRESH_STABLE=false
WAIT_STABLE=false
TUNER_REQUESTS=()
dvb_select_all=false
dvb_selection_defaults=()
TARGET_CT=''
CT_GID=''
SERVICE_USER=''
APPLICATION=''
FIRMWARE=false
INSTALL_FIRMWARE=false
FIRMWARE_SINCE='15 minutes ago'
# Host paths are fixed here; test fixtures source the helpers without this block.
SYS_ROOT=/sys
DEV_ROOT=/dev
STABLE_ROOT=/var/lib/dvb-passthrough
FIRMWARE_DIR=/lib/firmware
FIRMWARE_BACKUP_ROOT=/var/backups/dvb-passthrough
OPERATION_LOCK=/run/lock/dvb-passthrough.lock
while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --yes) ASSUME_YES=true ;;
    --verbose) VERBOSE=true ;;
    --no-restart) : ;;
    --remove|-r) REMOVE=true ;;
    --stable) STABLE=true ;;
    --tuner)
      (($# >= 2)) || { msg_error '--tuner needs a USB identity, serial, or path ID'; exit 1; }
      TUNER_REQUESTS+=("$2"); shift ;;
    --all-tuners) dvb_select_all=true ;;
    --refresh-stable) REFRESH_STABLE=true ;;
    --wait) WAIT_STABLE=true ;;
    --firmware) FIRMWARE=true ;;
    --install-firmware) FIRMWARE=true; INSTALL_FIRMWARE=true ;;
    --firmware-since)
      (($# >= 2)) || { msg_error "$1 needs a value"; exit 1; }
      FIRMWARE_SINCE=$2; shift ;;
    --app)
      (($# >= 2)) || { msg_error '--app needs plex or tvheadend'; exit 1; }
      [[ -z $APPLICATION || $APPLICATION == "$2" ]] || { msg_error 'Conflicting --app arguments'; exit 1; }
      case "$2" in plex|tvheadend) APPLICATION=$2 ;; *) msg_error 'Use --app plex or --app tvheadend'; exit 1 ;; esac
      shift ;;
    --gid|--user)
      (($# >= 2)) || { msg_error "$1 needs a value"; exit 1; }
      if [[ $1 == --gid ]]; then CT_GID=$2; else SERVICE_USER=$2; fi
      shift ;;
    --help|-h)
      printf '%s\n' 'Usage: dvb-passthrough.sh [--app plex|tvheadend] [--tuner ID ... | --all-tuners] [--stable] [--dry-run] [--yes] [--verbose] [--no-restart] [--gid CONTAINER_GID] [--user NAME] [--remove] [CTID_OR_NAME]'
      printf '%s\n' 'Application defaults: plex -> plex; tvheadend -> hts. Explicit --user overrides the account; reruns reuse the saved profile.'
      printf '%s\n' '       dvb-passthrough.sh {--firmware|--install-firmware} [--firmware-since "15 minutes ago"] [--dry-run] [--yes]'
      exit 0 ;;
    -*) msg_error "Unknown option: $1"; exit 1 ;;
    *) [[ -z $TARGET_CT ]] || { msg_error 'Specify one target'; exit 1; }; TARGET_CT=$1 ;;
  esac
  shift
done
if $dvb_select_all && ((${#TUNER_REQUESTS[@]})); then
  msg_error 'Use --tuner or --all-tuners, not both'; exit 1
fi
if { $FIRMWARE || $REMOVE || $REFRESH_STABLE; } && { $dvb_select_all || ((${#TUNER_REQUESTS[@]})); }; then
  msg_error 'Tuner selection is only for container passthrough setup'; exit 1
fi
if $FIRMWARE && { $REMOVE || $STABLE || [[ -n $TARGET_CT || -n $CT_GID || -n $APPLICATION || -n $SERVICE_USER ]]; }; then
  msg_error 'Firmware setup is a separate host operation; omit the target, --gid, and --remove'; exit 1
fi
if $REFRESH_STABLE; then
  [[ $EUID == 0 && -z $TARGET_CT ]] && ! $STABLE && ! $FIRMWARE && ! $REMOVE && ! $DRY_RUN || exit 1
  shopt -s nullglob
  # Never wait inside udev itself. The boot service retries until firmware and
  # all members of multi-tuner devices have initialized (bounded to 60 seconds).
  deadline=$((SECONDS + 60))
  while :; do
    if (exec 8>/run/lock/dvb-passthrough-stable.lock; flock 8; stable_refresh_all); then exit 0; fi
    $WAIT_STABLE && ((SECONDS < deadline)) || exit 1
    sleep 1
  done
fi
$WAIT_STABLE && { msg_error '--wait is reserved for the stable refresh helper'; exit 1; }
[[ -z $SERVICE_USER || $SERVICE_USER =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ ]] || { msg_error 'Invalid service user'; exit 1; }
[[ -z $CT_GID || $CT_GID =~ ^[0-9]+$ ]] || { msg_error 'Invalid GID'; exit 1; }
# --- Privilege Check ---
if [[ $EUID -ne 0 ]]; then
  msg_error "This script must be executed as root on the host system."
  exit 1
fi

# ==============================================================================
# 0. PLATFORM DETECTION (Proxmox VE vs Incus)
# ==============================================================================
PLATFORM="unknown"
if command -v pct &>/dev/null && [[ -d "/etc/pve" ]]; then
  PLATFORM="proxmox"
elif command -v incus &>/dev/null; then
  PLATFORM="incus"
  # Host discovery must never be applied to the client's default remote.
  incus() {
    if [[ -n ${INCUS_PROJECT:-} ]]; then command incus --force-local --project "$INCUS_PROJECT" "$@"
    else command incus --force-local "$@"; fi
  }
fi

# ==============================================================================
[[ $PLATFORM != unknown ]] || { msg_error 'Neither Proxmox VE nor Incus detected'; exit 1; }
for utility in readlink stat sort awk sha256sum mktemp flock udevadm; do
  command -v "$utility" >/dev/null || { msg_error "Missing prerequisite: $utility"; exit 1; }
done
if $FIRMWARE; then
  firmware_workflow
  exit 0
fi
if [[ -z $TARGET_CT ]]; then
  if [[ $PLATFORM == proxmox ]]; then pct list; else incus list; fi
  read -r -p 'Target Container ID/Name: ' TARGET_CT || exit 1
fi
CT_RUNNING=false
if [[ $PLATFORM == proxmox ]]; then
  [[ $TARGET_CT =~ ^[1-9][0-9]*$ ]] || { msg_error 'Invalid CTID'; exit 1; }
  CT_STATE=$(pct status "$TARGET_CT")
  [[ $CT_STATE == 'status: running' ]] && CT_RUNNING=true
else
  incus_target_context || exit 1
fi
if [[ $PLATFORM == proxmox ]]; then
  STATE="/var/lib/dvb-passthrough/proxmox/$TARGET_CT"
fi
[[ ! -f $STATE/stable-map ]] || STABLE=true
if $STABLE; then
  [[ $PLATFORM == proxmox ]] || { msg_error '--stable currently requires Proxmox'; exit 1; }
  command -v systemctl >/dev/null || { msg_error '--stable requires systemd'; exit 1; }
fi

if $REMOVE; then
  msg_info "Remove tracked container passthrough from $TARGET_CT"
  $DRY_RUN && exit 0
  if ! $ASSUME_YES; then
    read -r -p 'Remove tracked passthrough? [y/N] ' answer || exit 1
    [[ $answer == y || $answer == Y ]] || exit 0
  fi
  umask 077
  exec 9>/run/lock/dvb-passthrough.lock
  flock -n 9 || exit 1
  mkdir -p /var/backups/dvb-passthrough
  BACKUP=$(mktemp -d /var/backups/dvb-passthrough/removal.XXXXXXXX)
  backup_tracking
  [[ ! -f $STATE/stable-map ]] || cp -- "$STATE/stable-map" "$BACKUP/stable-map"
  if [[ $PLATFORM == proxmox ]]; then
    CONF_FILE="/etc/pve/lxc/$TARGET_CT.conf"
    [[ -r $CONF_FILE && -w $CONF_FILE ]] || exit 1
    ! grep -qE '^(lock:|\[)' "$CONF_FILE" || { msg_error 'Locked CT or snapshot/pending sections'; exit 1; }
    cp -- "$CONF_FILE" "$BACKUP/container.conf"
    BEFORE=$(sha256sum "$CONF_FILE")
    CLEAN=$(pve_clean_config "$CONF_FILE")
    pve_save_config "$CLEAN"
    if [[ -f $STATE/stable-map ]]; then
      (exec 8>/run/lock/dvb-passthrough-stable.lock; flock 8; stable_clear_map "$STATE/stable-map"; rm -- "$STATE/stable-map")
    fi
  else
    incus config show "$TARGET_CT" > "$BACKUP/container.yaml"
    load_incus_tracking
    for key in "${TRACKED_DEVICES[@]}"; do remove_incus_device "$key"; done
  fi
  rm -f -- "$STATE/selection" "$STATE/profile"
  shopt -s nullglob
  dvb_discover_tuners
  dvb_power_update false
  dvb_remove_container_banner
  msg_ok "Tracked entries removed. Backup: $BACKUP"
  msg_warn 'Power rules for other assignments retained. Legacy group/firmware/node modes were not tracked; review manually. No restart performed; live device access persists until the source container is stopped/started.'
  exit 0
fi
dvb_resolve_profile
if [[ $APPLICATION == tvheadend && $dvb_default_account == true ]] && $CT_RUNNING; then
  SERVICE_USER=$(run_in_ct bash -c "$(declare -f dvb_tvheadend_user); dvb_tvheadend_user") || {
    msg_error 'Cannot resolve the TVHeadend daemon account; review the service or supply --user'; exit 1;
  }
fi
if [[ -z $CT_GID ]]; then
  if $CT_RUNNING; then
    CT_GID=$(run_in_ct id -g "$SERVICE_USER") || {
      msg_error "Cannot resolve $SERVICE_USER; supply --gid"; exit 1;
    }
  else
    msg_error 'Stopped container: supply --gid with the intended in-container group ID'
    exit 1
  fi
fi
[[ $CT_GID =~ ^[0-9]+$ && ${#CT_GID} -le 10 ]] || { msg_error 'Invalid container GID'; exit 1; }
CT_GID=$((10#$CT_GID))
((CT_GID <= 2147483647)) || { msg_error 'GID out of range'; exit 1; }


if [[ $PLATFORM == proxmox ]]; then
  CONF_FILE="/etc/pve/lxc/$TARGET_CT.conf"
  [[ -r $CONF_FILE && -w $CONF_FILE ]] || { msg_error 'Cannot access CT configuration'; exit 1; }
  ! grep -qE '^(lock:|\[)' "$CONF_FILE" || { msg_error 'CT locked or has pending/snapshot sections'; exit 1; }
  HELP=$(pct help set --verbose 2>&1)
  [[ $HELP == *'--dev'* ]] || { msg_error 'Native dev entries unsupported'; exit 1; }
  # Detect unsafe legacy tracking before making host changes, including dry runs.
  CLEAN=$(pve_clean_config "$CONF_FILE")
fi
# 1. DETECT ACTUAL KERNEL-REGISTERED DVB DEVICES
# ==============================================================================
shopt -s nullglob
dvb_discover_tuners
PASSTHROUGH_DEVICES=()
if [[ -f $STATE/selection ]]; then
  mapfile -t dvb_selection_defaults < "$STATE/selection"
elif [[ -f $STATE/stable-map ]]; then
  mapfile -t dvb_selection_defaults < <(awk '{print "usb:" $2 ":" $3 ":" $4}' "$STATE/stable-map" | sort -u)
fi
if ((${#dvb_tuner_roots[@]})); then
  if dvb_select_tuners "${TUNER_REQUESTS[@]}"; then :
  else
    rc=$?
    [[ $rc != 2 ]] || exit 0
    exit "$rc"
  fi
  PASSTHROUGH_DEVICES=("${dvb_selected_nodes[@]}")
  mapfile -t PASSTHROUGH_DEVICES < <(printf '%s\n' "${PASSTHROUGH_DEVICES[@]}" | sort -u)
  msg_info 'Selected physical DVB devices:'
  printf '  %s\n' "${dvb_selected_ids[@]}"
  if $VERBOSE; then printf '  %s\n' "${PASSTHROUGH_DEVICES[@]}"; fi
elif ((${#TUNER_REQUESTS[@]})); then
  msg_error 'Selected tuners are unavailable: no initialized DVB devices found'; exit 1
fi
dvb_assignment_preflight "$PLATFORM" "$TARGET_CT"
if [[ $PLATFORM == proxmox ]]; then
  dvb_check_pve_file <(printf '%s\n' "$CLEAN") 'unmanaged target configuration'
else
  incus_validate_unmanaged_sources
fi
# Do not exit here: stale managed power rules must be cleared even when no DVB
# devices remain. Driver/firmware diagnostics follow; no vendor-based guesses.

# 2. FIX: PREVENT USB AUTOSUSPEND (Prevents missed recordings / sleep dropouts)
# ==============================================================================
# Power policy is derived from the union of this selection and saved assignments.

msg_info "Plan: container $TARGET_CT, application $APPLICATION, user $SERVICE_USER, GID $CT_GID, mode 0660; no automatic restarts"
if $STABLE; then
  $CT_RUNNING || { msg_error 'Stable setup requires a running CT to install its application-discovery startup helper; start it first'; exit 1; }
  stable_plan
  msg_info 'Persistent USB serial map (slot vendor product serial channel adapter-count nodes):'
  printf '  %s\n' "${STABLE_PLAN[@]}"
fi
if (( ${#PASSTHROUGH_DEVICES[@]} )); then printf '  %s\n' "${PASSTHROUGH_DEVICES[@]}"; fi
if $DRY_RUN; then
  if (( ${#PASSTHROUGH_DEVICES[@]} == 0 )); then
    msg_warn 'No registered tuner devices. Apply would reconcile managed power rules, retain saved reconnect rules, then stop without adding passthrough.'
  fi
  exit 0
fi
if ! $ASSUME_YES; then
  read -r -p 'Apply this plan? [y/N] ' answer || exit 1
  [[ $answer == y || $answer == Y ]] || exit 0
fi
umask 077
exec 9>/run/lock/dvb-passthrough.lock
flock -n 9 || { msg_error 'Another DVB operation is running'; exit 1; }
# Recheck after acquiring the host lock; another build may have claimed a tuner
# while this operation was awaiting confirmation. Rediscover to reject unplugged
# or renumbered devices rather than applying stale paths.
dvb_selection_defaults=("${dvb_selected_ids[@]}")
dvb_discover_tuners
if ((${#dvb_selection_defaults[@]})); then
  dvb_select_all=false
  dvb_select_tuners "${dvb_selection_defaults[@]}"
  PASSTHROUGH_DEVICES=("${dvb_selected_nodes[@]}")
fi
dvb_assignment_preflight "$PLATFORM" "$TARGET_CT"
if [[ $PLATFORM == proxmox ]]; then
  CLEAN=$(pve_clean_config "$CONF_FILE")
  dvb_check_pve_file <(printf '%s\n' "$CLEAN") 'unmanaged target configuration'
else
  incus_validate_unmanaged_sources
fi
if $STABLE; then stable_plan; fi
mkdir -p /var/backups/dvb-passthrough
HOST_BACKUP=$(mktemp -d /var/backups/dvb-passthrough/host.XXXXXXXX)
for file in /etc/udev/rules.d/99-tvtuner-power.rules /etc/udev/rules.d/99-dvb-permissions.rules /etc/modules-load.d/dvb-tuners.conf; do
  [[ ! -f $file ]] || cp -- "$file" "$HOST_BACKUP/"
done
msg_info "Host backup: $HOST_BACKUP"
msg_info 'Updating USB autosuspend policy for all managed tuner assignments'
dvb_power_update

# ==============================================================================
# 3-5. CONSERVATIVE HOST CONFIGURATION
# ==============================================================================
# Retire only the exact legacy global permission rule emitted by this utility.
LEGACY_RULE=/etc/udev/rules.d/99-dvb-permissions.rules
if [[ -f $LEGACY_RULE ]]; then
  EXPECTED=$'# Automatically generated: Allow unprivileged LXC access to DVB and Media device nodes\nSUBSYSTEM=="dvb", MODE="0666", GROUP="video"\nSUBSYSTEM=="media", MODE="0666", GROUP="video"'
  [[ $(cat "$LEGACY_RULE") == "$EXPECTED" ]] || {
    msg_error 'Unrecognized permission rule; review it manually before proceeding'; exit 1;
  }
  rm -- "$LEGACY_RULE"
  udevadm control --reload-rules
  msg_warn 'Legacy global 0666 rule removed. Existing host modes remain until reconnect/reboot; original modes cannot safely be guessed.'
fi
# Host firmware setup is a separate, explicitly selected operation. Only already
# initialized tuner hardware is passed here; modules and blacklists are untouched.
if (( ${#PASSTHROUGH_DEVICES[@]} == 0 )); then
  msg_error 'No kernel-registered DVB character devices. Check attached-device kernel logs, driver binding and firmware requirements, then retry.'
  msg_info 'Use --firmware for device-specific missing-firmware diagnosis, or --install-firmware to review supported downloads.'
  exit 1
fi

# 6. VERIFY DEVICE NODES ON HOST
# ==============================================================================
if [[ ! -d "/dev/dvb" ]]; then
  msg_error "Device node '/dev/dvb' was not created by the kernel."
  msg_warn 'Check kernel logs for the attached tuner.'
  exit 1
fi

ADAPTER_COUNT=$(find /sys/class/dvb -maxdepth 1 -name 'dvb*.frontend0' 2>/dev/null | wc -l)
msg_ok "Found ${ADAPTER_COUNT} active DVB adapter node(s) in /dev/dvb"

# ==============================================================================
# Target selection was completed before host changes.

# 8. APPLY NATIVE PER-DEVICE PASSTHROUGH
# ==============================================================================
# Use an existing in-container group; never rewrite its numeric GID to the host
# GID. Explicit --gid allows configuring stopped containers without starting them.
BACKUP_DIR=/var/backups/dvb-passthrough
mkdir -p "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR"
BACKUP=$(mktemp -d "$BACKUP_DIR/operation.XXXXXXXX")
backup_tracking
[[ ! -f $STATE/stable-map ]] || cp -- "$STATE/stable-map" "$BACKUP/stable-map"
if $STABLE; then stable_install; fi
if [[ $PLATFORM == proxmox ]]; then
  CONF_FILE="/etc/pve/lxc/$TARGET_CT.conf"
  [[ -r $CONF_FILE && -w $CONF_FILE ]] || { msg_error 'Cannot access CT configuration'; exit 1; }
  ! grep -qE '^(lock:|\[)' "$CONF_FILE" || { msg_error 'CT is locked or has pending/snapshot sections; review before proceeding'; exit 1; }
  HELP=$(pct help set --verbose 2>&1)
  [[ $HELP == *'--dev'* ]] || { msg_error 'Native dev entries are not supported by this pct version'; exit 1; }
  cp -- "$CONF_FILE" "$BACKUP/container.conf"
  BEFORE=$(sha256sum "$CONF_FILE")
  CLEAN=$(pve_clean_config "$CONF_FILE")
  NEW_ENTRIES=()
  index=0
  for dev in "${PASSTHROUGH_DEVICES[@]}"; do
    [[ $CLEAN != *"$dev"* && $CLEAN != *'/dev/dvb '* ]] || { msg_error "Existing passthrough conflicts with $dev"; exit 1; }
    while grep -qE "^dev$index:" <<< "$CLEAN"; do index=$((index + 1)); done
    ((index < 256)) || { msg_error 'No free dev slot'; exit 1; }
    NEW_ENTRIES+=("dev$index: path=$dev,uid=0,gid=$CT_GID,mode=0660")
    index=$((index + 1))
  done
  # Do not edit the same CT concurrently in the Proxmox UI.
  CONFIGURATION=$(printf '%s\n\n' "$CLEAN"; printf '%s\n' "${NEW_ENTRIES[@]}")
  pve_save_config "$CONFIGURATION" "${NEW_ENTRIES[@]}"
else
  incus config show "$TARGET_CT" > "$BACKUP/container.yaml"
  configure_incus_devices
fi
msg_ok "Device configuration saved. Backup: $BACKUP"
write_tracking "$STATE/selection" "${dvb_selected_ids[@]}"
write_tracking "$STATE/profile" "$APPLICATION"$'\t'"$SERVICE_USER"
dvb_power_update
if $STABLE && [[ -f $BACKUP/stable-map ]]; then
  stable_prune_previous "$BACKUP/stable-map"
fi
dvb_install_container_banner
if $CT_RUNNING; then
  run_in_ct bash -c "$(declare -f dvb_store_library)
    mkdir -p /usr/local/lib/community-scripts /var/backups/dvb-passthrough || exit
    permissions_source=\$(cat) || exit
    dvb_store_library /usr/local/lib/community-scripts/dvb.func \"\$permissions_source\"" < <(dvb_permissions_library)
fi

# 9. VERIFY WITHOUT RESTARTING OR OPENING ACTIVE TUNERS
# ==============================================================================
if ! $CT_RUNNING; then
  msg_warn 'Container stopped: verification deferred until you start it.'
  exit 0
fi
VERIFIED=true
if [[ $PLATFORM == proxmox && -s $BACKUP/tracked-devices && $(cat "$BACKUP/tracked-devices") != "$(printf '%s\n' "${NEW_ENTRIES[@]}")" ]]; then
  msg_warn 'The previous device mapping changed. Stop/start this container to revoke its old live nodes before reassigning removed tuners.'
  VERIFIED=false
fi
for dev in "${PASSTHROUGH_DEVICES[@]}"; do
  if ! run_in_ct test -c "$dev"; then
    msg_warn "Character device not yet visible: $dev"
    VERIFIED=false
    continue
  fi
  if [[ $(stat -Lc '%t:%T' "$dev") != "$(run_in_ct stat -Lc '%t:%T' "$dev")" ]]; then
    msg_warn "Container device number differs from host: $dev (restart required)"
    VERIFIED=false
    continue
  fi
  # Expand the positional argument in the container's shell.
  # shellcheck disable=SC2016
  if ! run_in_ct runuser -u "$SERVICE_USER" -- sh -c 'test -r "$1" && test -w "$1"' sh "$dev"; then
    msg_warn "Access not verified for $SERVICE_USER: $dev (check user/group and runuser availability)"
    VERIFIED=false
  fi
done
if ! $VERIFIED; then
  msg_warn 'Configuration saved; runtime verification incomplete. Schedule a restart when recordings permit and recheck device access.'
  exit 2
fi
if [[ $APPLICATION == tvheadend ]]; then
  if ! run_in_ct bash -c "$(declare -f dvb_verify_service); dvb_verify_service \"\$1\" tvheadend.service" sh "$SERVICE_USER"; then
    msg_warn 'Device configuration saved; the TVHeadend daemon identity/groups need attention. No restart performed.'
    exit 2
  fi
fi
msg_ok "Character devices and read/write permissions verified as $SERVICE_USER"
if [[ $APPLICATION == tvheadend ]]; then
  msg_info 'Next: check TVHeadend Configuration > DVB Inputs > TV adapters. Networks, channel scans and Plex HDHomeRun setup remain application configuration.'
fi
msg_info 'Opening/tuning a frontend and recording are not tested automatically.'
if $STABLE; then
  msg_info 'Serial aliases refresh at boot and on DVB device events. Reconnecting hardware while the CT is running may require a CT restart.'
else
  msg_info 'Rerun discovery if USB ports or adapter numbers change; --stable enables persistent USB serial mappings on Proxmox.'
fi
