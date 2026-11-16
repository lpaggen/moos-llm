#!/usr/bin/env bash

set -e
cd "$(dirname "$0")" || exit 1
#------------------------------------------------------------
#   Script: launch.sh
#  Mission: m2_crossing
#   Author: M.Benjamin
#   LastEd: May 2024
#------------------------------------------------------------
#  Part 1: Set convenience functions for producing terminal
#          debugging output, and catching SIGINT (ctrl-c).
#------------------------------------------------------------
vecho() { if [ "$VERBOSE" != "" ]; then echo "$ME: $1"; fi }
on_exit() { echo; echo "$ME: Halting all apps"; kill -- -$$; }
trap on_exit SIGINT
trap on_exit SIGTERM

#------------------------------------------------------------
#  Part 2: Set global variable default values
#------------------------------------------------------------
ME=`basename "$0"`
CMD_ARGS=""
TIME_WARP=1
VERBOSE=""
JUST_MAKE=""
LOG_CLEAN=""
# Empty means all enabled vehicles from the fleet configuration.
VAMT=""
CONFIG_FILE="scenarios/open_waters/fleet.ini"
TIFF_FILE="scenarios/open_waters/denhelder_texel.tif"
OBSTACLE_FILE=""
RAND_VPOS=""
MAX_SPD=""
MMOD=""
START_DEPLOYED="false"
START_PAUSED="false"
CONFIG_VALUE="../tools/config_value.py"
MOOS_BASE_PORT=$(python3 "$CONFIG_VALUE" ports moos_base)
PSHARE_BASE_PORT=$(python3 "$CONFIG_VALUE" ports pshare_base)
MOOS_HOST=$(python3 "$CONFIG_VALUE" hosts moos)

# Monte
XLAUNCHED="no"
NOGUI=""

# Custom

#------------------------------------------------------------
#  Part 3: Check for and handle command-line arguments
#------------------------------------------------------------
for ARGI; do
    CMD_ARGS+=" ${ARGI}"
    if [ "${ARGI}" = "--help" -o "${ARGI}" = "-h" ]; then
	echo "$ME [OPTIONS] [time_warp]                      "
	echo "                                               "
	echo "Options:                                       "
	echo "  --help, -h         Show this help message    " 
	echo "  --verbose, -v      Verbose, confirm launch   "
	echo "  --just_make, -j    Only create targ files    " 
	echo "  --log_clean, -lc   Run clean.sh bef launch   " 
	echo "  --amt=N            Launch first N (default: all enabled)"
	echo "  --config=FILE      Fleet INI file             "
	echo "  --tiff=FILE        pMarineViewer chart; use 'none' for no chart"
	echo "  --obstacles=FILE   Static obstacle .moos file (default: chart companion)"
	echo "  --rand, -r         Rand vehicle positions    "
	echo "  --max_spd=N        Max helm/sim speed        "
	echo "  --mmod=<mod>       Mission variation/mod     "
	echo "  --start-deployed   Initialize vehicle helms deployed"
	echo "  --start-paused     Initialize vehicle simulators paused"
	echo "                                               "
	echo "Options (monte):                               "
	echo "  --xlaunched, -x    Launched by xlaunch       "
	echo "  --nogui, -ng       Headless launch, no gui   "
	echo "                                               "
	echo "Options (custom):                              "
	echo "  --fast, -f         Start head-on encounter   " 
	exit 0
    elif [ "${ARGI//[^0-9]/}" = "$ARGI" -a "$TIME_WARP" = 1 ]; then 
        TIME_WARP=$ARGI
    elif [ "${ARGI}" = "--verbose" -o "${ARGI}" = "-v" ]; then
	VERBOSE=$ARGI
    elif [ "${ARGI}" = "--just_make" -o "${ARGI}" = "-j" ]; then
	JUST_MAKE=$ARGI
    elif [ "${ARGI}" = "--log_clean" -o "${ARGI}" = "-lc" ]; then
	LOG_CLEAN=$ARGI
    elif [ "${ARGI:0:6}" = "--amt=" ]; then
        VAMT="${ARGI#--amt=*}"
    elif [ "${ARGI:0:9}" = "--config=" ]; then
        CONFIG_FILE="${ARGI#--config=}"
    elif [ "${ARGI:0:7}" = "--tiff=" ]; then
        TIFF_FILE="${ARGI#--tiff=}"
    elif [ "${ARGI:0:12}" = "--obstacles=" ]; then
        OBSTACLE_FILE="${ARGI#--obstacles=*}"
    elif [ "${ARGI}" = "--rand" -o "${ARGI}" = "-r" ]; then
        RAND_VPOS=$ARGI
    elif [ "${ARGI:0:10}" = "--max_spd=" ]; then
        MAX_SPD="${ARGI#--max_spd=*}"
    elif [ "${ARGI:0:7}" = "--mmod=" ]; then
        MMOD="${ARGI#--mmod=}"
    elif [ "${ARGI}" = "--start-deployed" ]; then
        START_DEPLOYED="true"
    elif [ "${ARGI}" = "--start-paused" ]; then
        START_PAUSED="true"

    elif [ "${ARGI}" = "--xlaunched" -o "${ARGI}" = "-x" ]; then
	XLAUNCHED="yes"
    elif [ "${ARGI}" = "--nogui" -o "${ARGI}" = "-ng" ]; then
	NOGUI="--nogui"
    elif [ "${ARGI}" = "--fast" -o "${ARGI}" = "-f" ]; then
	FAST=$ARGI
    else 
	echo "$ME: Bad arg:" $ARGI "Exit Code 1."
	exit 1
    fi
done

# pMarineViewer's metadata lookup literally replaces ".tif" with ".info";
# a ".tiff" suffix therefore becomes ".infof" and cannot work.
if [ "$TIFF_FILE" != "none" ]; then
    if [[ "$TIFF_FILE" != *.tif ]]; then
        echo "$ME: TIFF background must use the .tif suffix: $TIFF_FILE"
        exit 2
    fi
    TIFF_INFO="${TIFF_FILE%.tif}.info"
    if [ ! -r "$TIFF_FILE" ] || [ ! -r "$TIFF_INFO" ]; then
        echo "$ME: Background requires readable files: $TIFF_FILE and $TIFF_INFO"
        exit 2
    fi
fi

# By default, use the polygon editor's companion file when present. Missions
# without one use a valid empty include so pObstacleMgr can remain configured.
if [ -z "$OBSTACLE_FILE" ]; then
    OBSTACLE_FILE="empty_obstacles.moos"
    if [ "$TIFF_FILE" != "none" ]; then
        CHART_OBSTACLES="${TIFF_FILE%.tif}.collision.moos"
        [ -r "$CHART_OBSTACLES" ] && OBSTACLE_FILE="$CHART_OBSTACLES"
    fi
fi
if [ ! -r "$OBSTACLE_FILE" ]; then
    echo "$ME: obstacle file is not readable: $OBSTACLE_FILE"
    exit 2
fi

#------------------------------------------------------------
#  Part 4: Set starting positions, speeds, vnames, colors
#------------------------------------------------------------
INIT_ARGS=("--config=$CONFIG_FILE")
[ -n "$VAMT" ] && INIT_ARGS+=("--amt=$VAMT")
[ -n "$RAND_VPOS" ] && INIT_ARGS+=("$RAND_VPOS")
[ -n "$VERBOSE" ] && INIT_ARGS+=("$VERBOSE")
[ -n "$FAST" ] && INIT_ARGS+=("$FAST")
./init_field.sh "${INIT_ARGS[@]}"

mapfile -t VEHPOS < vpositions.txt
mapfile -t SPEEDS < vspeeds.txt
mapfile -t MAX_SPEEDS < vmaxspeeds.txt
mapfile -t VNAMES < vnames.txt
mapfile -t VCOLOR < vcolors.txt
mapfile -t VTYPE < vtypes.txt
mapfile -t VAVOID < vavoids.txt
mapfile -t LOIPOS < vloiterpos.txt
mapfile -t VDEST < vwaypoints.txt
mapfile -t VROUTES < vroutes.txt
MAX_VAMT=${#VNAMES[@]}
VAMT=$MAX_VAMT

#------------------------------------------------------------
#  Part 5: If verbose, show vars and confirm before launching
#------------------------------------------------------------
if [ "${VERBOSE}" != "" ]; then
    echo "============================================"
    echo "  $ME SUMMARY                   (ALL)       "
    echo "============================================"
    echo "CMD_ARGS =      [${CMD_ARGS}]               "
    echo "TIME_WARP =     [${TIME_WARP}]              "
    echo "JUST_MAKE =     [${JUST_MAKE}]              "
    echo "LOG_CLEAN =     [${LOG_CLEAN}]              "
    echo "VAMT =          [${VAMT}]                   "
    echo "MAX_VAMT =      [${MAX_VAMT}]               "
    echo "CONFIG_FILE =   [${CONFIG_FILE}]            "
    echo "TIFF_FILE =     [${TIFF_FILE}]              "
    echo "OBSTACLE_FILE = [${OBSTACLE_FILE}]          "
    echo "RAND_VPOS =     [${RAND_VPOS}]              "
    echo "MAX_SPD =       [${MAX_SPD}]                "
    echo "MMOD =          [${MMOD}]                   "
    echo "--------------------------------(VProps)----"
    echo "VNAMES =        [${VNAMES[*]}]              "
    echo "VCOLORS =       [${VCOLOR[*]}]              "
    echo "START_POS =     [${VEHPOS[*]}]              "
    echo "--------------------------------(Monte)-----"
    echo "XLAUNCHED =     [${XLAUNCHED}]              "
    echo "NOGUI =         [${NOGUI}]                  "
    echo "--------------------------------(Custom)----"
    echo "LOITER_POS  =   [${LOIPOS[*]}]              "
    echo "CROSS_POINT =   [${VDEST[*]}]              "
    echo "CROSS_ROUTE =   [${VROUTES[*]}]            "
    echo "                                            "
    echo -n "Hit any key to continue launch           "
    read ANSWER
fi

#------------------------------------------------------------
#  Part 6: Launch the Vehicles
#------------------------------------------------------------
for IX in $(seq 1 "$VAMT");
do
    IXX=$((IX - 1))
    MOOS_PORT=$((MOOS_BASE_PORT + IX))
    PSHARE_PORT=$((PSHARE_BASE_PORT + IX))
    VEH_MAX_SPD="${MAX_SPEEDS[$IXX]}"
    if [ -n "$MAX_SPD" ]; then
        VEH_MAX_SPD="$MAX_SPD"
    fi

    IVARGS=(--sim --auto "--max_spd=$VEH_MAX_SPD" "$TIME_WARP")
    [ -n "$JUST_MAKE" ] && IVARGS+=("$JUST_MAKE")
    [ -n "$VERBOSE" ] && IVARGS+=("$VERBOSE")
    [ -n "$MMOD" ] && IVARGS+=("--mmod=$MMOD")
    IVARGS+=("--start_deployed=$START_DEPLOYED")
    IVARGS+=("--start_paused=$START_PAUSED")
    IVARGS+=("--ip=$MOOS_HOST" "--shore=$MOOS_HOST")
    IVARGS+=("--shore_pshare=$PSHARE_BASE_PORT")
    IVARGS+=("--mport=$MOOS_PORT" "--pshare=$PSHARE_PORT")
    # Initialise uSimMarine at the scenario's declared speed.  Relying only on
    # pHelm to accelerate from zero introduces a large, warp-dependent startup
    # transient before the first decision (and at high warp can consume the
    # entire encounter before helm deployment settles).
    VEH_START_POS="${VEHPOS[$IXX]}"
    if [[ "$VEH_START_POS" != *"speed="* && "$VEH_START_POS" != *"spd="* ]]; then
        VEH_START_POS+=",speed=${SPEEDS[$IXX]}"
    fi
    IVARGS+=("--start_pos=$VEH_START_POS" "--stock_spd=${SPEEDS[$IXX]}")
    IVARGS+=("--vname=${VNAMES[$IXX]}" "--color=${VCOLOR[$IXX]}" "--vtype=${VTYPE[$IXX]}")
    IVARGS+=("--avoid=${VAVOID[$IXX]}")
    IVARGS+=("--obstacles=$OBSTACLE_FILE")
    IVARGS+=("--loipos=${LOIPOS[$IXX]}" "--cross_point=${VDEST[$IXX]}")
    IVARGS+=("--cross_route=points=${VROUTES[$IXX]}")
    vecho "Launching vehicle: ${IVARGS[*]}"

    ./launch_vehicle.sh "${IVARGS[@]}"
    sleep 0.5
done

#------------------------------------------------------------
#  Part 7: Launch the Shoreside mission file
#------------------------------------------------------------
VNAMES_COLON=$(IFS=:; echo "${VNAMES[*]}")
SARGS=" --auto --ip=$MOOS_HOST --mport=$MOOS_BASE_PORT --pshare=$PSHARE_BASE_PORT $NOGUI --vnames=$VNAMES_COLON --tiff=$TIFF_FILE "
SARGS+=" $TIME_WARP $JUST_MAKE $VERBOSE "
[ -n "$MMOD" ] && SARGS+=" --mmod=$MMOD "
vecho "Launching shoreside: $SARGS"
./launch_shoreside.sh $SARGS 

if [ "${JUST_MAKE}" != "" ]; then
    echo "$ME: Targ files made; exiting without launch."
    exit 0
fi

#------------------------------------------------------------
#  Part 8: Unless auto-launched, launch uMAC until mission quit
#------------------------------------------------------------
if [ "${XLAUNCHED}" != "yes" ]; then
    uMAC --paused targ_shoreside.moos
    trap "" SIGINT
    echo; echo "$ME: Halting all apps"
    kill -- -$$
fi

exit 0
