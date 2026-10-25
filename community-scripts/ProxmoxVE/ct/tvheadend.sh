#!/usr/bin/env bash
_cs_boot="${COMMUNITY_SCRIPTS_CORE_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../core}/core/build.func"
# shellcheck source=/dev/null
source "$_cs_boot" 2>/dev/null || source <(curl -fsSL "${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}/core/build.func")
# Copyright (c) 2021-2026 community-scripts ORG
# Author: Nicolas Pastorello (opastorello)
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Source: https://tvheadend.org/ | Github: https://github.com/tvheadend/tvheadend

APP="Tvheadend"
var_tags="${var_tags:-media;pvr;tv;dvr}"
var_cpu="${var_cpu:-2}"
var_ram="${var_ram:-2048}"
var_disk="${var_disk:-8}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
#var_arm64="${var_arm64:-no}" # unset = ask the user; set yes/no only when verified
var_unprivileged="${var_unprivileged:-1}"
var_dvb="${var_dvb:-no}"
var_dvb_tuners="${var_dvb_tuners:-}"
var_dvb_gid="${var_dvb_gid:-44}"

export var_admin_user="${var_admin_user:-admin}"
export var_admin_pass="${var_admin_pass:-$(openssl rand -base64 18 | tr -dc 'a-zA-Z0-9' | cut -c1-13)}"

header_info "$APP"
variables
color
catch_errors

function prepare_tvheadend_dvb() {
  local node pid uid entry
  dvb_tvh_user=''
  for node in /dev/dvb/adapter*/*; do
    [[ ! -c $node ]] || dvb_tvh_user=hts
  done
  [[ -n $dvb_tvh_user || -f /var/lib/dvb-passthrough/banner-devices.tsv ]] || return 0
  if [[ -x /usr/local/sbin/dvb-passthrough-aliases ]]; then
    /usr/local/sbin/dvb-passthrough-aliases || return 1
  fi
  if [[ -r /usr/local/lib/community-scripts/dvb-banner.func ]]; then
    source /usr/local/lib/community-scripts/dvb-banner.func || return 1
    dvb_banner_install || return 1
  fi
  if [[ -r /usr/local/lib/community-scripts/dvb.func ]]; then
    source /usr/local/lib/community-scripts/dvb.func || return 1
    dvb_tvh_user=$(dvb_tvheadend_user) || return 1
    setup_dvb "$dvb_tvh_user" || return 1
    dvb_verify_roster || return 1
  else
    # Older containers can still be updated without downloading host helpers.
    pid=$(systemctl show tvheadend.service -p MainPID --value) || return 1
    [[ $pid =~ ^[1-9][0-9]*$ ]] || return 1
    uid=$(awk '/^Uid:/ {print $3}' "/proc/$pid/status") || return 1
    [[ $uid != 0 ]] || return 1
    entry=$(getent passwd "$uid") || return 1
    dvb_tvh_user=${entry%%:*}
  fi
  for node in /dev/dvb/adapter*/*; do
    [[ -c $node ]] || continue
    runuser -u "$dvb_tvh_user" -- test -r "$node" && runuser -u "$dvb_tvh_user" -- test -w "$node" || return 1
  done
}

function verify_tvheadend_dvb_access() {
  local node
  systemctl is-active --quiet tvheadend || return 1
  [[ -n ${dvb_tvh_user:-} ]] || return 0
  if declare -F dvb_verify_service >/dev/null; then
    dvb_verify_service "$dvb_tvh_user" tvheadend.service || return 1
  else
    for node in /dev/dvb/adapter*/*; do
      [[ -c $node ]] || continue
      runuser -u "$dvb_tvh_user" -- test -r "$node" && runuser -u "$dvb_tvh_user" -- test -w "$node" || return 1
    done
  fi
}

function update_script() {
  header_info
  check_container_storage
  check_container_resources

  if [[ ! -d /var/lib/tvheadend ]]; then
    msg_error "No ${APP} Installation Found!"
    exit
  fi

  msg_info "Updating Tvheadend"
  $STD apt update || return 1
  $STD apt install -y tvheadend || return 1
  prepare_tvheadend_dvb || { msg_error "Tvheadend package updated, but DVB preparation failed"; return 1; }
  msg_ok "Updated Tvheadend"

  msg_info "Restarting Service"
  systemctl restart tvheadend || return 1
  verify_tvheadend_dvb_access || { msg_error "Tvheadend DVB runtime verification failed"; return 1; }
  msg_ok "Restarted Service"
  msg_ok "Updated successfully!"
  exit
}

start
build_container
description

msg_ok "Completed Successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW}Access it using the following URL:${CL}"
echo -e "${GATEWAY}${BGN}http://${IP}:9981${CL}"
echo -e "${INFO}${YW}Admin Username: ${var_admin_user}${CL}"
echo -e "${INFO}${YW}Admin Password: ${var_admin_pass}${CL}"
