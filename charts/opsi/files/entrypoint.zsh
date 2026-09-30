#!/usr/bin/env zsh
unsetopt BG_NICE
# Import the upstream functions without starting the server yet.
source /entrypoint.sh set_environment_vars

# Configure local service IDs before upstream moves its image directories.
# Native OPSI permission checks stay enabled for both local and file storage.
if [[ "${OPSI_CHART_FILE_STORAGE}" == "true" ]]; then
    /usr/bin/python3 /opt/opsi-chart/file-storage.py || exit $?
fi

# Upstream set_host_id precedes init_volumes. Bind the persisted identity first,
# so a recovered /data cannot be mistaken for the image's seed identity.
typeset -g opsi_chart_initial_volume_setup=0
for volume_path in /etc/opsi /var/lib/opsi /var/log/opsi /var/lib/opsiconfd; do
    [[ -L "${volume_path}" ]] || opsi_chart_initial_volume_setup=1
done
init_volumes
typeset -g opsi_chart_initial_volume_result=$?

# entrypoint calls init_volumes again and uses its nonzero result as the signal
# to run opsi-set-rights after setup. Preserve that signal after our early bind.
functions[opsi_chart_upstream_init_volumes]=${functions[init_volumes]}
function init_volumes {
    opsi_chart_upstream_init_volumes
    local volume_result=$?
    if (( volume_result != 0 || opsi_chart_initial_volume_result != 0 || opsi_chart_initial_volume_setup != 0 )); then
        return 1
    fi
    return 0
}

# This worker waits for the native verified API and marks readiness only after
# identity reconciliation and CA merging have completed. Supervisor remains PID1.
/usr/bin/python3 /opt/opsi-chart/runtime.py bootstrap &
entrypoint
