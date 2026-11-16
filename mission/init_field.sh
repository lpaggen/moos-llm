#!/bin/bash
#------------------------------------------------------------
#   Script: init_field.sh
#   Purpose: Generate mission field files deterministically
#------------------------------------------------------------

set -e

#------------------------------------------------------------
#  Part 1: Helpers
#------------------------------------------------------------
ME=$(basename "$0")
cd "$(dirname "$0")" || exit 1

vecho() {
    if [ "$VERBOSE" != "" ]; then
        echo "$ME: $1"
    fi
}

kill_moos_apps() {
    echo "$ME: Killing stale MOOS-IvP processes"
    # Every process launched by the vehicle and shoreside ANTLER blocks must
    # be stopped. Leaving even one broker/PID/host-info process alive lets it
    # reconnect to the next MOOSDB under the same client name and prevents
    # fresh node reports from reaching pMarineViewer.
    pkill -9 -f 'pAntler|MOOSDB|uSimMarine|iM300|pHelmIvP|pMarinePID|pMarineViewer|pNodeReporter|pShare|pHostInfo|pRealm|pContactMgr|pObstacleMgr|uFldNodeBroker|uFldShoreBroker|uFldNodeComms|uProcessWatch|uLoadWatch|uMemWatch|uTimerScript|uMAC|uXMS|uQueryDB|uPokeDB' 2>/dev/null || true
}

#------------------------------------------------------------
#  Part 2: Defaults
#------------------------------------------------------------
# Empty means all enabled vehicles from the fleet configuration.
VEHICLE_AMT=""
CONFIG_FILE="scenarios/open_waters/fleet.ini"
VERBOSE=""
RAND_VPOS="no"
FAST="no"
KILL_APPS="yes"

#------------------------------------------------------------
#  Part 4: Args
#------------------------------------------------------------
for ARGI in "$@"; do
    if [ "${ARGI}" = "--help" ] || [ "${ARGI}" = "-h" ]; then
        echo "$ME [OPTIONS]"
        echo
        echo "Options:"
        echo "  --amt=N            Launch first N (default: all enabled)"
        echo "  --config=FILE      Fleet INI file (default: scenarios/open_waters/fleet.ini)"
        echo "  --verbose, -v      Verbose, confirm values"
        echo "  --rand, -r         Accepted but ignored"
        echo "  --fast, -f         Use FAST_* values above"
        echo "  --no_kill          Do not kill stale MOOS apps"
        exit 0

    elif [ "${ARGI:0:6}" = "--amt=" ]; then
        VEHICLE_AMT="${ARGI#--amt=}"

    elif [ "${ARGI:0:9}" = "--config=" ]; then
        CONFIG_FILE="${ARGI#--config=}"

    elif [ "${ARGI}" = "--verbose" ] || [ "${ARGI}" = "-v" ]; then
        VERBOSE="$ARGI"

    elif [ "${ARGI}" = "--rand" ] || [ "${ARGI}" = "-r" ]; then
        RAND_VPOS="yes"

    elif [ "${ARGI}" = "--fast" ] || [ "${ARGI}" = "-f" ]; then
        FAST="yes"

    elif [ "${ARGI}" = "--no_kill" ]; then
        KILL_APPS="no"

    else
        echo "$ME: Bad Arg: $ARGI. Exit Code 1."
        exit 1
    fi
done

#------------------------------------------------------------
#  Part 5: Safety checks
#------------------------------------------------------------
if [ -n "$VEHICLE_AMT" ] && ! [[ "$VEHICLE_AMT" =~ ^[0-9]+$ ]]; then
    echo "$ME: Bad vehicle amount: $VEHICLE_AMT"
    exit 2
fi

if [ -n "$VEHICLE_AMT" ] && [ "$VEHICLE_AMT" -lt 1 ]; then
    echo "$ME: Vehicle amount must be positive."
    exit 2
fi

#------------------------------------------------------------
#  Part 6: Validate and render fleet files
#------------------------------------------------------------
RENDER_ARGS=(--config "$CONFIG_FILE" --output-dir .)
[ -n "$VEHICLE_AMT" ] && RENDER_ARGS+=(--amt "$VEHICLE_AMT")
if [ "$FAST" = "yes" ]; then
    RENDER_ARGS+=(--fast)
fi
python3 render_fleet.py "${RENDER_ARGS[@]}"

#------------------------------------------------------------
#  Part 7: Kill stale apps only after configuration is valid
#------------------------------------------------------------
if [ "$KILL_APPS" = "yes" ]; then
    kill_moos_apps
fi

#------------------------------------------------------------
#  Part 8: Verbose output
#------------------------------------------------------------
if [ "${VERBOSE}" != "" ]; then
    echo "--------------------------------------"
    echo "VEHICLE_AMT = ${VEHICLE_AMT:-all enabled}"
    echo "CONFIG_FILE  = $CONFIG_FILE"
    echo "RAND_VPOS   = $RAND_VPOS"
    echo "FAST        = $FAST"
    echo "KILL_APPS   = $KILL_APPS"
    echo "--------------------------------------(pos/spd)"
    echo "vpositions.txt:"; cat vpositions.txt
    echo "vspeeds.txt:";    cat vspeeds.txt
    echo "vmaxspeeds.txt:"; cat vmaxspeeds.txt
    echo "--------------------------------------(vprops)"
    echo "vnames.txt:";     cat vnames.txt
    echo "vcolors.txt:";    cat vcolors.txt
    echo "vtypes.txt:";     cat vtypes.txt
    echo "vavoids.txt:";    cat vavoids.txt
    echo "--------------------------------------(custom)"
    echo "vwaypoints.txt:"; cat vwaypoints.txt
    echo "vloiterpos.txt:"; cat vloiterpos.txt
    echo -n "Hit any key to continue"
    read ANSWER
fi

exit 0
