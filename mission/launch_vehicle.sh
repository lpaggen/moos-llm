#!/bin/bash
set -e
#------------------------------------------------------------ 
#   Script: launch_vehicle.sh
#  Mission: m2_crossing
#   Author: M.Benjamin
#   LastEd: May 2024
#------------------------------------------------------------ 
#  Part 1: Set convenience functions for producing terminal
#          debugging output, and catching SIGINT (ctrl-c).
#------------------------------------------------------------ 
cd "$(dirname "$0")" || exit 1

vecho() { if [ "$VERBOSE" != "" ]; then echo "$ME: $1"; fi }
on_exit() { echo; echo "$ME: Halting all apps"; kill -- -$$; }
trap on_exit SIGINT
trap on_exit SIGTERM

#------------------------------------------------------------
#  Part 2: Declare global var defaults
#------------------------------------------------------------
ME=`basename "$0"`
CMD_ARGS=""
TIME_WARP=1
VERBOSE=""
JUST_MAKE=""
LOG_CLEAN="no"
AUTO_LAUNCHED="no"

CONFIG_VALUE="../tools/config_value.py"
IP_ADDR=$(python3 "$CONFIG_VALUE" hosts moos)
MOOS_BASE_PORT=$(python3 "$CONFIG_VALUE" ports moos_base)
PSHARE_BASE_PORT=$(python3 "$CONFIG_VALUE" ports pshare_base)
MOOS_PORT=$((MOOS_BASE_PORT + 1))
PSHARE_PORT=$((PSHARE_BASE_PORT + 1))
SHORE_IP="$IP_ADDR"
SHORE_PSHARE="$PSHARE_BASE_PORT"
MMOD=""
START_DEPLOYED="false"
START_PAUSED="false"

VNAME="abe"
COLOR="yellow"
VTYPE="ship"
AVOID="true"
OBSTACLE_FILE="empty_obstacles.moos"
XMODE="M300"
START_POS="x=0,y=0,heading=0"  
STOCK_SPD="1.4"
MAX_SPD="2"
SPEED_FACTOR="50"

# Custom
LOITER_POS="0,-50"
CROSS_POINT="x=90,y=0"  
CROSS_ROUTE="point=${CROSS_POINT}"
ROUTE_EDGE_COLOR="invisible"
ROUTE_VERTEX_COLOR="invisible"
ROUTE_VERTEX_SIZE="0"
ROUTE_NEXTPT_COLOR="invisible"
OBJECTIVE_RADIUS="20"
OBJECTIVE_NM_RADIUS="40"

#------------------------------------------------------------
#  Part 3: Check for and handle command-line arguments
#------------------------------------------------------------
for ARGI; do
    CMD_ARGS+=" ${ARGI}"
    if [ "${ARGI}" = "--help" -o "${ARGI}" = "-h" ]; then
	echo "$ME [OPTIONS] [time_warp]                        "
	echo "                                                 " 
	echo "Options:                                         "
	echo "  --help, -h             Show this help message  "
	echo "  --just_make, -j        Only create targ files  "
	echo "  --verbose, -v          Verbose, confirm launch "
	echo "  --log_clean, -lc       Run clean.sh bef launch "
        echo "  --auto, -a             Script launch, no uMAC  "
	echo "                                                 "
	echo "  --ip=<localhost>       Veh Default IP addr     "
	echo "  --mport=<${MOOS_PORT}>         Veh MOOSDB port         "
	echo "  --pshare=<${PSHARE_PORT}>        Veh pShare listen port  "
	echo "  --shore=<localhost>    Shoreside IP to try     "
	echo "  --shore_pshare=<${SHORE_PSHARE}>  Shoreside pShare port   "
        echo "  --mmod=<mod>           Mission variation/mod   "
	echo "  --start_deployed=<true|false> Initial helm deployment"
	echo "  --start_paused=<true|false> Initial simulator pause state"
	echo "                                                 "
	echo "  --vname=<abe>          Veh name given          "
	echo "  --color=<yellow>       Veh color given         "
	echo "  --avoid=<true|false>  Enable collision avoidance"
	echo "  --obstacles=<file>   pObstacleMgr given_obstacle lines"
	echo "  --sim, -s              Sim, not fld vehicle    "
	echo "  --start_pos=<X,Y,Hdg>  Sim start pos/hdg       "
	echo "  --stock_spd=<m/s>      Default vehicle speed   "
	echo "  --max_spd=<m/s>        Max Sim and Helm speed  "
	echo "                                                 "
	echo "Options (custom):                                "
	echo "  --loipos=<X,Y>        Loiter poly center x/y  "
	echo "  --cross_point=<X,Y>  Crossing waypoint endpoint "
	exit 0;
    elif [ "${ARGI//[^0-9]/}" = "$ARGI" -a "$TIME_WARP" = 1 ]; then 
        TIME_WARP=$ARGI
    elif [ "${ARGI}" = "--verbose" -o "${ARGI}" = "-v" ]; then
        VERBOSE="yes"
    elif [ "${ARGI}" = "--just_make" -o "${ARGI}" = "-j" ]; then
	JUST_MAKE="yes"
    elif [ "${ARGI}" = "--log_clean" -o "${ARGI}" = "-lc" ]; then
	LOG_CLEAN="yes"
    elif [ "${ARGI}" = "--auto" -o "${ARGI}" = "-a" ]; then
        AUTO_LAUNCHED="yes" 

    elif [ "${ARGI:0:5}" = "--ip=" ]; then
        IP_ADDR="${ARGI#--ip=*}"
    elif [ "${ARGI:0:7}" = "--mport" ]; then
	MOOS_PORT="${ARGI#--mport=*}"
    elif [ "${ARGI:0:9}" = "--pshare=" ]; then
        PSHARE_PORT="${ARGI#--pshare=*}"
    elif [ "${ARGI:0:8}" = "--shore=" ]; then
        SHORE_IP="${ARGI#--shore=*}"
    elif [ "${ARGI:0:15}" = "--shore_pshare=" ]; then
        SHORE_PSHARE="${ARGI#--shore_pshare=*}"
    elif [ "${ARGI:0:7}" = "--mmod=" ]; then
	MMOD="${ARGI#--mmod=}"
    elif [ "${ARGI:0:17}" = "--start_deployed=" ]; then
        START_DEPLOYED="${ARGI#--start_deployed=*}"
    elif [ "${ARGI:0:15}" = "--start_paused=" ]; then
        START_PAUSED="${ARGI#--start_paused=*}"

    elif [ "${ARGI:0:8}" = "--vname=" ]; then
        VNAME="${ARGI#--vname=*}"
    elif [ "${ARGI:0:8}" = "--vtype=" ]; then
        VTYPE="${ARGI#--vtype=}"
    elif [ "${ARGI:0:8}" = "--color=" ]; then
	COLOR="${ARGI#--color=*}"
    elif [ "${ARGI:0:8}" = "--avoid=" ]; then
	AVOID="${ARGI#--avoid=*}"
        if [ "$AVOID" != "true" ] && [ "$AVOID" != "false" ]; then
            echo "$ME: --avoid must be true or false"
            exit 2
        fi
    elif [ "${ARGI:0:12}" = "--obstacles=" ]; then
        OBSTACLE_FILE="${ARGI#--obstacles=*}"
    elif [ "${ARGI}" = "--sim" -o "${ARGI}" = "-s" ]; then
        XMODE="SIM"
    elif [ "${ARGI:0:12}" = "--start_pos=" ]; then
        START_POS="${ARGI#--start_pos=*}"
    elif [ "${ARGI:0:12}" = "--stock_spd=" ]; then
        STOCK_SPD="${ARGI#--stock_spd=*}"
    elif [ "${ARGI:0:10}" = "--max_spd=" ]; then
        MAX_SPD="${ARGI#--max_spd=*}"

    elif [ "${ARGI:0:9}" = "--loipos=" ]; then
        LOITER_POS="${ARGI#--loipos=*}"
    elif [ "${ARGI:0:14}" = "--cross_point=" ]; then
        CROSS_POINT="${ARGI#--cross_point=*}"
    elif [ "${ARGI:0:14}" = "--cross_route=" ]; then
        CROSS_ROUTE="${ARGI#--cross_route=*}"

    else 
	echo "$ME: Bad Arg:[$ARGI]. Exit Code 1."
	exit 1
    fi
done

# pMarinePID emits thrust in the range 0..100. Scale it so MAX_SPD maps to
# full thrust instead of imposing the old fixed 5 m/s ceiling.
SPEED_FACTOR=$(awk -v max_speed="$MAX_SPD" 'BEGIN {
    if (max_speed <= 0) exit 1
    printf "%.10g", 100 / max_speed
}') || {
    echo "$ME: --max_spd must be greater than zero"
    exit 2
}

#------------------------------------------------------------
#  Part 4: If Heron hardware, set key info based on IP address
#------------------------------------------------------------
if [ "${XMODE}" = "M300" ]; then
    COLOR=`get_heron_info.sh --color --hint=$COLOR`
    IP_ADDR=`get_heron_info.sh --ip`
    FSEAT_IP=`get_heron_info.sh --fseat`
    VNAME=`get_heron_info.sh --name`
    if [ $? != 0 ]; then
	echo "$ME: Problem getting Heron Info. Exit Code 2"
	exit 2
    fi
fi

if [ ! -r "$OBSTACLE_FILE" ]; then
    echo "$ME: obstacle file is not readable: $OBSTACLE_FILE"
    exit 2
fi
OBSTACLE_FILE=$(realpath "$OBSTACLE_FILE")
OBSTACLE_DIR=$(dirname "$OBSTACLE_FILE")
OBSTACLE_INCLUDE=$(basename "$OBSTACLE_FILE")
     
#------------------------------------------------------------
#  Part 5: If verbose, show vars and confirm before launching
#------------------------------------------------------------
if [ "${VERBOSE}" = "yes" ]; then 
    echo "============================================"
    echo "     launch_vehicle.sh SUMMARY        $VNAME"
    echo "============================================"
    echo "$ME                               "
    echo "CMD_ARGS =      [${CMD_ARGS}]     "
    echo "TIME_WARP =     [${TIME_WARP}]    "
    echo "JUST_MAKE =     [${JUST_MAKE}]    "
    echo "AUTO_LAUNCHED = [${AUTO_LAUNCHED}]"
    echo "LOG_CLEAN =     [${LOG_CLEAN}]    "
    echo "----------------------------------"
    echo "IP_ADDR =       [${IP_ADDR}]      "
    echo "MOOS_PORT =     [${MOOS_PORT}]    "
    echo "PSHARE_PORT =   [${PSHARE_PORT}]  "
    echo "SHORE_IP =      [${SHORE_IP}]     "
    echo "SHORE_PSHARE =  [${SHORE_PSHARE}] "
    echo "MMOD =          [${MMOD}]         "
    echo "----------------------------------"
    echo "VNAME =         [${VNAME}]        "
    echo "COLOR =         [${COLOR}]        "
    echo "VTYPE =         [${VTYPE}]        "
    echo "OBSTACLE_FILE = [${OBSTACLE_FILE}]"
    echo "XMODE =         [${XMODE}]        "
    echo "------------Sim-------------------"
    echo "START_POS =     [${START_POS}]    "
    echo "STOCK_SPD =     [${STOCK_SPD}]    "
    echo "MAX_SPD =       [${MAX_SPD}]      "
    echo "------------Fld-------------------"
    echo "FSEAT_IP =      [${FSEAT_IP}]     "
    echo "------------Custom----------------"
	    echo "LOITER_POS =    [${LOITER_POS}]   "
	    echo "CROSS_POINT =   [${CROSS_POINT}]   "
	    echo "CROSS_ROUTE =   [${CROSS_ROUTE}]   "
    echo -n "Hit any key to continue launching $VNAME "
    read ANSWER
fi

#------------------------------------------------------------
#  Part 6: If Log clean before launch, do it now. 
#------------------------------------------------------------
if [ "$LOG_CLEAN" = "yes" -a -f "clean.sh" ]; then
    vecho "Cleaning local Log Files"
    ./clean.sh
fi

#------------------------------------------------------------
#  Part 7: Create the .moos and .bhv files. 
#------------------------------------------------------------
if [ "$VNAME" = "LLM_SHIP" ]; then
    ROUTE_EDGE_COLOR="gray60"
    ROUTE_VERTEX_COLOR="white"
    ROUTE_VERTEX_SIZE="3"
    ROUTE_NEXTPT_COLOR="yellow"
    OBJECTIVE_RADIUS="200"
    OBJECTIVE_NM_RADIUS="250"
fi

NSFLAGS="--strict --force"
if [ "${AUTO_LAUNCHED}" = "no" ]; then
    NSFLAGS="--interactive --force"
fi

nsplug meta_vehicle.moos targ_$VNAME.moos $NSFLAGS --path="$OBSTACLE_DIR" WARP=$TIME_WARP \
       IP_ADDR=$IP_ADDR             MOOS_PORT=$MOOS_PORT \
       PSHARE_PORT=$PSHARE_PORT     SHORE_IP=$SHORE_IP   \
       SHORE_PSHARE=$SHORE_PSHARE   VNAME=$VNAME         \
       COLOR=$COLOR                 XMODE=$XMODE         \
       START_POS=$START_POS         MAX_SPD=$MAX_SPD     \
       START_PAUSED=$START_PAUSED                         \
       SPEED_FACTOR=$SPEED_FACTOR                         \
       MMOD=$MMOD                                        \
       FSEAT_IP=$FSEAT_IP                                \
       VTYPE=$VTYPE                 AVOID=$AVOID          \
       OBSTACLE_FILE=$OBSTACLE_INCLUDE

nsplug meta_vehicle.bhv targ_$VNAME.bhv $NSFLAGS  \
       START_POS=$START_POS         VNAME=$VNAME  \
       STOCK_SPD=$STOCK_SPD         MMOD=$MMOD    AVOID=$AVOID \
       START_DEPLOYED=$START_DEPLOYED \
       LOITER_POS=$LOITER_POS       CROSS_POINT=$CROSS_POINT \
       CROSS_ROUTE="$CROSS_ROUTE" \
       ROUTE_EDGE_COLOR=$ROUTE_EDGE_COLOR \
       ROUTE_VERTEX_COLOR=$ROUTE_VERTEX_COLOR \
       ROUTE_VERTEX_SIZE=$ROUTE_VERTEX_SIZE \
       ROUTE_NEXTPT_COLOR=$ROUTE_NEXTPT_COLOR \
       OBJECTIVE_RADIUS=$OBJECTIVE_RADIUS \
       OBJECTIVE_NM_RADIUS=$OBJECTIVE_NM_RADIUS

if [ "${JUST_MAKE}" = "yes" ]; then
    echo "$ME: Targ files made; exiting without launch."
    exit 0
fi

#------------------------------------------------------------
#  Part 8: Launch the vehicle mission
#------------------------------------------------------------
echo "Launching $VNAME MOOS Community. WARP="$TIME_WARP
pAntler targ_$VNAME.moos >& /dev/null &
echo "Done Launching $VNAME MOOS Community"

#------------------------------------------------------------
#  Part 9: If launched from script, we're done, exit now
#------------------------------------------------------------
if [ "${AUTO_LAUNCHED}" = "yes" ]; then
    exit 0
fi

#------------------------------------------------------------
# Part 10: Launch uMAC until the mission is quit
#------------------------------------------------------------
uMAC targ_$VNAME.moos
trap "" SIGINT
kill -- -$$
