### instructions

This directory contains prompts for the agents to work with. You can change them to experiment with their behavior, but understand that changing the output payload of an agent will break the system if you do not adapt the rest of the agents to work with the updated payload.

Payload contracts do not belong in this directory. Their canonical Pydantic
models and generated JSON Schema artifacts live in `../schemas/`.
