"""Generate integration patches against the revisions in upstream-revisions.json.

Usage: python3 tools/generate_community_patches.py /path/to/upstream-downloads
The directory contains core/{pve,incus,ui} and ProxmoxVE/{ct,install}.
Patched sources are also written below that directory for syntax/apply checks.
"""

import difflib
from pathlib import Path
import re
import sys
import subprocess
import json
import io
import tarfile

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(sys.argv[1])
COMMUNITY = ROOT / "community-scripts"


REVISIONS = json.loads((COMMUNITY / "upstream-revisions.json").read_text())


def baseline_text(repo, path):
    checkout = BASE / repo
    if (checkout / ".git").exists():
        return subprocess.check_output(["git", "show", f"{REVISIONS[repo]}:{path}"],
                                       cwd=checkout, text=True)
    return (checkout / path).read_text(encoding="utf-8")


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Expected one upstream anchor: {old!r}")
    return text.replace(old, new, 1)


def emit(repo, path, changed, patch):
    original = baseline_text(repo, path)
    output = BASE / "patched" / repo / path
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(changed, newline="\n")
    difference = "".join(difflib.unified_diff(original.splitlines(True), changed.splitlines(True),
                                           fromfile=f"a/{path}", tofile=f"b/{path}"))
    (COMMUNITY / repo / patch).write_text(difference, newline="\n")


LOADER = """if ! declare -F dvb_community_select >/dev/null; then
  _cs_source_func "lib/dvb-selection.func" || return 1
fi
"""



def indent(text, spaces):
    return "".join(" " * spaces + line if line.strip() else line for line in text.splitlines(True))


pve = baseline_text("core", "pve/backend.func")
pve_hook = '''  # Configure only selected DVB devices, with cross-container ownership checks.
  configure_dvb_passthrough() {
    [[ "${ENABLE_DVB:-${var_dvb:-no}}" == yes ]] || return 0
''' + indent(LOADER, 4) + '''    local help
    help=$(pct help set --verbose 2>&1) || return 1
    [[ $help == *'--dev'* ]] || { msg_error 'Native devN passthrough is required'; return 1; }
    dvb_community_select || return 1
    dvb_community_pve_config "$LXC_CONFIG" || return 1
    export dvb_configured=1
    msg_ok "DVB passthrough: ${#dvb_selected_ids[@]} selected physical device(s)"
  }

'''
pve = replace_once(pve, "  # Configure USB passthrough for privileged containers\n", pve_hook + "  # Configure USB passthrough for privileged containers\n")
pve = replace_once(pve, "  configure_gpu_passthrough\n", "  configure_gpu_passthrough\n  configure_dvb_passthrough || return 1\n")
pve = replace_once(pve, '  export ENABLE_GPU="$ENABLE_GPU"\n', '  export ENABLE_GPU="$ENABLE_GPU"\n  export ENABLE_DVB="${ENABLE_DVB:-${var_dvb:-no}}"\n')
emit("core", "pve/backend.func", pve, "pve/backend_dvb.patch")

incus = baseline_text("core", "incus/backend.func")
hook = '''  if [[ "${ENABLE_DVB:-${var_dvb:-no}}" == yes ]]; then
''' + indent(LOADER, 4) + '''    dvb_community_select || return 1
    dvb_community_incus_config "$CT_NAME" || return 1
    msg_ok "DVB passthrough: ${#dvb_selected_ids[@]} selected physical device(s)"
  fi
'''
incus = replace_once(incus, "incus_create_lxc_container() {\n", 'incus_create_lxc_container() {\n  if [[ "${ENABLE_DVB:-${var_dvb:-no}}" == yes && $EUID != 0 ]]; then\n    msg_error "DVB host device management requires root; rerun on the host as root or disable DVB"\n    return 1\n  fi\n')
incus = replace_once(incus, "  return 0\n}\n\nincus_wait_for_network()", hook + "  return 0\n}\n\nincus_wait_for_network()")
incus = replace_once(incus, '  export ENABLE_GPU="${ENABLE_GPU:-no}"\n', '  export ENABLE_GPU="${ENABLE_GPU:-no}"\n  export ENABLE_DVB="${ENABLE_DVB:-${var_dvb:-no}}"\n')
incus = replace_once(incus, '    ENABLE_GPU="${ENABLE_GPU:-no}"\n', '    ENABLE_GPU="${ENABLE_GPU:-no}"\n    ENABLE_DVB="${ENABLE_DVB:-${var_dvb:-no}}"\n')
emit("core", "incus/backend.func", incus, "incus/backend_dvb.patch")

ui = baseline_text("core", "ui/advanced.func")
ui = replace_once(ui, "  local MAX_STEP=31\n", "  local MAX_STEP=32\n")
# Move existing steps together, including their comments. Back navigation stays
# in the same wizard rather than adding a second prompt after confirmation.
ui = re.sub(r"^(    )(\d+)(\))", lambda m: m[1] + str(int(m[2]) + (int(m[2]) >= 22)) + m[3], ui, flags=re.M)
ui = re.sub(r"STEP (\d+):", lambda m: f"STEP {int(m[1]) + (int(m[1]) >= 22)}:", ui)
ui = replace_once(ui, '  local _enable_gpu="${var_gpu:-no}"\n', '  local _enable_gpu="${var_gpu:-no}"\n  local _enable_dvb="${var_dvb:-no}"\n  local _dvb_tuners="${dvb_tuners:-${var_dvb_tuners:-}}"\n')
step = '''    # STEP 22: DVB TV Tuner Passthrough (opt-in)
    22)
      local dvb_default_flag="--defaultno"
      [[ "$_enable_dvb" == yes ]] && dvb_default_flag=""
      if whiptail --backtitle "Proxmox VE Helper Scripts [Step $STEP/$MAX_STEP]" \\
        --title "DVB / TV TUNER PASSTHROUGH" --ok-button "Next" --cancel-button "Back" \\
        $dvb_default_flag --yesno "Connect DVB tuners to this container?\\n\\nChoose physical devices for Plex, TVHeadend or another media server.\\nAll adapters of each selected device are included." 14 74; then
        _enable_dvb=yes
''' + indent(LOADER, 8) + '''        dvb_tuners="$_dvb_tuners"
        if dvb_selection_ui=whiptail dvb_community_select --prompt; then
          _dvb_tuners="$dvb_tuners"
        else
          continue
        fi
      else
        if [[ $? == 1 ]]; then _enable_dvb=no
        else ((STEP--)); continue; fi
      fi
      ((STEP++))
      ;;

'''
keyctl = ui.index("    # STEP 23: Keyctl Support")
header = ui.rfind("    # ═", 0, keyctl)
ui = ui[:header] + step + ui[header:]
ui = replace_once(ui, '  GPU: $_enable_gpu | Protection: $protect_desc', '  GPU: $_enable_gpu | DVB: $_enable_dvb ($_dvb_tuners) | Protection: $protect_desc')
ui = replace_once(ui, '  ENABLE_GPU="$_enable_gpu"\n', '  ENABLE_GPU="$_enable_gpu"\n  export ENABLE_DVB="$_enable_dvb" dvb_tuners="$_dvb_tuners"\n')
ui = replace_once(ui, '  var_gpu="$_enable_gpu"\n', '  var_gpu="$_enable_gpu"\n  export var_dvb="$_enable_dvb" var_dvb_tuners="$_dvb_tuners"\n')
ui = replace_once(ui, '  log_msg "GPU Passthrough: ${ENABLE_GPU:-no}"\n', '  log_msg "GPU Passthrough: ${ENABLE_GPU:-no}"\n  log_msg "DVB Passthrough: ${ENABLE_DVB:-no} (${dvb_tuners:-})"\n')
emit("core", "ui/advanced.func", ui, "ui/advanced_dvb.patch")

tvh = baseline_text("ProxmoxVE", "ct/tvheadend.sh")
tvh = replace_once(tvh, 'source "$_cs_boot"', '# shellcheck source=/dev/null\nsource "$_cs_boot"')
tvh = replace_once(tvh, 'var_unprivileged="${var_unprivileged:-1}"\n', 'var_unprivileged="${var_unprivileged:-1}"\nvar_dvb="${var_dvb:-no}"\nvar_dvb_tuners="${var_dvb_tuners:-}"\nvar_dvb_gid="${var_dvb_gid:-44}"\n')
tvh_helper = '''function prepare_tvheadend_dvb() {
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

'''
tvh = replace_once(tvh, "function update_script() {\n", tvh_helper + "function update_script() {\n")
tvh = replace_once(tvh, '  $STD apt update\n  $STD apt install -y tvheadend\n', '  $STD apt update || return 1\n  $STD apt install -y tvheadend || return 1\n  prepare_tvheadend_dvb || { msg_error "Tvheadend package updated, but DVB preparation failed"; return 1; }\n')
tvh = replace_once(tvh, '  systemctl restart tvheadend\n', '  systemctl restart tvheadend || return 1\n  verify_tvheadend_dvb_access || { msg_error "Tvheadend DVB runtime verification failed"; return 1; }\n')
(COMMUNITY / "ProxmoxVE/ct/tvheadend.sh").write_text(tvh, newline="\n")
emit("ProxmoxVE", "ct/tvheadend.sh", tvh, "ct/tvheadend_dvb.patch")

# Both installers use one permissions/banner implementation. Downloads can be
# replaced by a mounted local core checkout inside the container.
def installer_hook(app, user):
    resolve = '  dvb_user=$(dvb_tvheadend_user) || exit 1\n' if app == "TVHeadend" else f'  dvb_user={user}\n'
    return '''# Configure only devices selected by the host DVB backend.
if [[ ${ENABLE_DVB:-${var_dvb:-no}} == yes || -d /dev/dvb ]]; then
  msg_info "Configuring Selected DVB Devices for ''' + app + '''"
  dvb_require_nodes || exit 1
''' + resolve + '''  dvb_prepare_container "$dvb_user" || exit 1
  if [[ ${dvb_groups_changed:-0} == 1 ]] && systemctl is-active --quiet ''' + ("tvheadend" if app == "TVHeadend" else "plexmediaserver") + '''; then
    systemctl restart ''' + ("tvheadend" if app == "TVHeadend" else "plexmediaserver") + ''' || exit 1
  fi
  msg_ok "Configured Selected DVB Devices for ''' + app + '''"
fi

'''


for name, app, user in (("plex", "Plex", "plex"), ("tvheadend", "TVHeadend", "hts")):
    path = f"install/{name}-install.sh"
    installer = baseline_text("ProxmoxVE", path)
    anchor = 'setup_hwaccel "plex"\n\n' if name == "plex" else 'msg_info "Starting Service"\n'
    installer = replace_once(installer, anchor, (anchor + installer_hook(app, user)) if name == "plex" else (installer_hook(app, user) + anchor))
    if name == "tvheadend":
        installer = replace_once(installer, '$STD apt install -y tvheadend\n', '$STD apt install -y tvheadend || exit 1\n')
        installer = replace_once(installer, 'systemctl enable -q --now tvheadend\n', 'systemctl enable -q --now tvheadend || exit 1\nif [[ -d /dev/dvb ]]; then dvb_verify_service "$dvb_user" tvheadend.service || exit 1; fi\n')
        (COMMUNITY / "ProxmoxVE" / path).write_text(installer, newline="\n")
    emit("ProxmoxVE", path, installer, f"install/{name}-install_dvb.patch")

plex = baseline_text("ProxmoxVE", "ct/plex.sh")
old = (COMMUNITY / "ProxmoxVE/ct/plex.sh").read_text()
helper = old[old.index("function verify_plex_dvb_access()"):old.index("function update_script()")]
helper = helper.replace('    if [[ ${dvb_groups_changed:-0} == 1 ]]; then\n      systemctl restart plexmediaserver || return 1\n    fi\n', '')
plex = replace_once(plex, 'var_gpu="${var_gpu:-yes}"\n', 'var_gpu="${var_gpu:-yes}"\nvar_dvb="${var_dvb:-no}"\nvar_dvb_tuners="${var_dvb_tuners:-}"\nvar_dvb_gid="${var_dvb_gid:-44}"\n')
plex = replace_once(plex, 'function update_script() {\n', helper + 'function update_script() {\n')
plex = replace_once(plex, '  $STD apt install -y plexmediaserver\n', '  $STD apt install -y plexmediaserver || return 1\n  verify_plex_dvb_access || { msg_error "Plex DVB preparation failed"; return 1; }\n')
plex = replace_once(plex, '  apt_update_safe\n', '  apt_update_safe || return 1\n')
(COMMUNITY / "ProxmoxVE/ct/plex.sh").write_text(plex, newline="\n")
emit("ProxmoxVE", "ct/plex.sh", plex, "ct/plex_dvb.patch")

for path, anchor in (("core/build.func", "_CS_ENGINE_FILES=(\n"), ("lxc/install.func", "_CS_CT_ENGINE_FILES=(\n")):
    changed = replace_once(baseline_text("core", path), anchor, anchor + "  lib/dvb-selection.func lib/dvb.func lib/dvb-banner.func\n")
    dest = COMMUNITY / "core" / (path.removesuffix(".func") + "_dvb.patch")
    dest.parent.mkdir(parents=True, exist_ok=True)
    emit("core", path, changed, path.removesuffix(".func") + "_dvb.patch")

# Public container helper API is loaded by the prepared runtime.
tools = baseline_text("core", "lib/tools.func")
tools += "\n_tools_source_part dvb-banner\n_tools_source_part dvb\n"
emit("core", "lib/tools.func", tools, "lib/tools_dvb.patch")

# The API snapshot must be derived from the actual public loader, not regexes.
import shutil
core_checkout = BASE / "patched/core"
if (BASE / "core/.git").exists():
    archive = subprocess.check_output(["git", "archive", REVISIONS["core"]], cwd=BASE / "core")
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        contents.extractall(core_checkout, filter="data")
else:
    for source in (BASE / "core").iterdir():
        if source.is_dir():
            shutil.copytree(source, core_checkout / source.name, dirs_exist_ok=True)
# Restore generated changes over the archived baseline.
for patch in sorted((COMMUNITY / "core").glob("*/*_dvb.patch")):
    if patch.name == "API_dvb.patch":
        continue
    subprocess.run(["git", "apply", str(patch.resolve())], cwd=core_checkout, check=True)
for source in (COMMUNITY / "core/lib").glob("*.func"):
    (core_checkout / "lib" / source.name).write_bytes(source.read_bytes())
api = subprocess.check_output(["bash", "-c", r'COMMUNITY_SCRIPTS_CORE_DIR="$PWD"; _cs_source_func() { source "$COMMUNITY_SCRIPTS_CORE_DIR/$1"; }; source lib/tools.func >/dev/null; declare -F | awk "{print \$3}" | grep -vE "^(_cs_source_func|_tools_source_part|_build_ui_source|_api_source_part)$" | sort'], cwd=core_checkout, text=True, timeout=60)
emit("core", "lib/API.txt", api, "lib/API_dvb.patch")
