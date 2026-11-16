SUPPORTED_ACTIONS = {
    "skip",
    "alter_course",
    "maintain_course",
    "rejoin_route",
    "resume",
    "set_speed",
    "send_vhf",
    "sound_horn",
    "stop",
}

NAVIGATION_ACTIONS = SUPPORTED_ACTIONS - {"send_vhf", "sound_horn"}
AUXILIARY_ACTIONS = {"send_vhf", "sound_horn"}
