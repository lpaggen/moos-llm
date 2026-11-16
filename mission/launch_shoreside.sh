#!/bin/bash 
#------------------------------------------------------------ 
#   Script: launch_shoreside.sh    
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
#  Part 2: Set global variable default values
#------------------------------------------------------------ 
ME=`basename "$0"`
CMD_ARGS=""
TIME_WARP=1
JUST_MAKE="no"
VERBOSE=""
AUTO_LAUNCHED="no"
LAUNCH_GUI="yes"

CONFIG_VALUE="../tools/config_value.py"
IP_ADDR=$(python3 "$CONFIG_VALUE" hosts moos)
MOOS_PORT=$(python3 "$CONFIG_VALUE" ports moos_base)
PSHARE_PORT=$(python3 "$CONFIG_VALUE" ports pshare_base)
MMOD=""
TIFF_FILE="scenarios/open_waters/denhelder_texel.tif"

VNAMES="abe:ben"

#------------------------------------------------------------ 
#  Part 3: Check for and handle command-line arguments
#------------------------------------------------------------ 
for ARGI; do
    CMD_ARGS+="${ARGI} "
    if [ "${ARGI}" = "--help" -o "${ARGI}" = "-h" ]; then
	echo "$ME: [OPTIONS] [time_warp]                     " 
	echo "                                               "
	echo "Options:                                       "
	echo "  --help, -h         Show this help message    " 
	echo "  --just_make, -j    Only create targ files    " 
	echo "  --verbose, -v      Verbose, confirm launch   "
	echo "  --tiff=<file>      pMarineViewer chart; 'none' disables it"
	echo "                                               "
        echo "  --auto, -a                                   "
        echo "    Auto-launched by a script.                 "
        echo "    Will not launch uMAC as the final step.    "
        echo "  --nogui, -n                                  "
        echo "    Headless mode - no pMarineViewer etc       "
	echo "                                               "
	echo "  --ip=<localhost>                             "
	echo "    Force pHostInfo to use this IP Address     "
	echo "  --mport=<${MOOS_PORT}>                               "
	echo "    Port number of this vehicle's MOOSDB port  "
	echo "  --pshare=<${PSHARE_PORT}>                              "
	echo "    Port number of this vehicle's pShare port  "
        echo "  --mmod=<mod>                                 "
        echo "    Identify a mission variation/mod           "
	echo "                                               "
        echo "  --vnames=<vnames>                            "
        echo "    Colon-separate list of all vehicle names   "
	exit 0;
    elif [ "${ARGI//[^0-9]/}" = "$ARGI" -a "$TIME_WARP" = 1 ]; then 
        TIME_WARP=$ARGI
    elif [ "${ARGI}" = "--just_make" -o "${ARGI}" = "-j" ]; then
	JUST_MAKE="yes"
    elif [ "${ARGI}" = "--verbose" -o "${ARGI}" = "-v" ]; then
	VERBOSE="yes"
    elif [ "${ARGI}" = "--auto" -o "${ARGI}" = "-a" ]; then
        AUTO_LAUNCHED="yes"
    elif [ "${ARGI}" = "--nogui" -o "${ARGI}" = "-n" ]; then
        LAUNCH_GUI="no"

    elif [ "${ARGI:0:5}" = "--ip=" ]; then
        IP_ADDR="${ARGI#--ip=*}"
    elif [ "${ARGI:0:7}" = "--mport" ]; then
	MOOS_PORT="${ARGI#--mport=*}"
    elif [ "${ARGI:0:9}" = "--pshare=" ]; then
        PSHARE_PORT="${ARGI#--pshare=*}"
    elif [ "${ARGI:0:7}" = "--mmod=" ]; then
	MMOD="${ARGI#--mmod=}"
    elif [ "${ARGI:0:7}" = "--tiff=" ]; then
        TIFF_FILE="${ARGI#--tiff=}"

    elif [ "${ARGI:0:9}" = "--vnames=" ]; then
        VNAMES="${ARGI#--vnames=*}"
    else
	echo "$ME: Bad Arg:[$ARGI]. Exit Code 1."
	exit 1
    fi
done

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

#------------------------------------------------------------ 
#  Part 4: If not auto_launched (likely running in the field),
#          and the IP_ADDR has not be explicitly set, try to get
#          it using the ipaddrs.sh script. 
#------------------------------------------------------------ 
if [ "${AUTO_LAUNCHED}" = "no" -a "${IP_ADDR}" = "localhost" ]; then
    MAYBE_IP_ADDR=`ipaddrs.sh --blunt`
    if [ $? = 0 ]; then
	IP_ADDR=$MAYBE_IP_ADDR
    fi
fi

#------------------------------------------------------------ 
#  Part 5: If verbose, show vars and confirm before launching
#------------------------------------------------------------ 
if [ "${VERBOSE}" = "yes" ]; then 
    echo "=================================="
    echo "  launch_shoreside.sh SUMMARY     "
    echo "=================================="
    echo "$ME                               "
    echo "CMD_ARGS =      [${CMD_ARGS}]     "
    echo "TIME_WARP =     [${TIME_WARP}]    "
    echo "JUST_MAKE =     [${JUST_MAKE}]    "
    echo "AUTO_LAUNCHED = [${AUTO_LAUNCHED}]"
    echo "----------------------------------"
    echo "IP_ADDR =       [${IP_ADDR}]      "
    echo "MOOS_PORT =     [${MOOS_PORT}]    "
    echo "PSHARE_PORT =   [${PSHARE_PORT}]  "
    echo "LAUNCH_GUI =    [${LAUNCH_GUI}]   "
    echo "MMOD =          [${MMOD}]         "
    echo "TIFF_FILE =     [${TIFF_FILE}]    "
    echo "----------------------------------"
    echo "VNAMES =        [${VNAMES}]       "
    echo "----------------------------------"
    echo -n "Hit any key to continue launch "
    read ANSWER
fi

#------------------------------------------------------------ 
#  Part 6: Create the shoreside mission file
#------------------------------------------------------------ 
NSFLAGS="--strict --force"
if [ "${AUTO_LAUNCHED}" = "no" ]; then
    NSFLAGS="--interactive --force"
fi

nsplug meta_shoreside.moos targ_shoreside.moos $NSFLAGS WARP=$TIME_WARP \
       IP_ADDR=$IP_ADDR             MOOS_PORT=$MOOS_PORT    \
       PSHARE_PORT=$PSHARE_PORT     LAUNCH_GUI=$LAUNCH_GUI  \
       MMOD=$MMOD                   VNAMES=$VNAMES          \
       TIFF_FILE=$TIFF_FILE

if [ "${JUST_MAKE}" = "yes" ]; then
    echo "$ME: Targ files made; exiting without launch."
    exit 0
fi

#------------------------------------------------------------ 
#  Part 7: Launch the shoreside MOOS community
#------------------------------------------------------------ 
echo "Launching Shoreside MOOS Community. WARP="$TIME_WARP
pAntler targ_shoreside.moos >& /dev/null &
# Give pAntler time to start MOOSDB before an auto/xlaunch parent exits.
# Without this, the shoreside process group can disappear while both vehicle
# communities continue running, leaving pMarineViewer with no node reports.
sleep 1
echo "Done Launching Shoreside Community"

#------------------------------------------------------------ 
#  Part 8: If launched from script, we're done, exit now
#------------------------------------------------------------ 
if [ "${AUTO_LAUNCHED}" = "yes" ]; then
    exit 0
fi

#------------------------------------------------------------ 
# Part 9: Launch uMAC until the mission is quit
#------------------------------------------------------------ 
uMAC targ_shoreside.moos
trap "" SIGINT
kill -- -$$
