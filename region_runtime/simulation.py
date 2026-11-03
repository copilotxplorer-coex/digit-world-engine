"""Node-side bounded integer fixture, NOT the existing game's movement rules."""
import copy
from .contract import state_valid, commands_valid

def simulate(base, commands):
    state_valid(base)
    commands_valid(commands, base)
    state = copy.deepcopy(base)
    walls = {tuple(p) for p in state["walls"]}
    for command in commands:
        old = state["positions"][command["actor"]]
        target = [old[0] + command["dx"], old[1] + command["dy"]]
        if max(abs(target[0]), abs(target[1])) > 10000:
            raise ValueError("Fixture boundary exceeded")
        if tuple(target) not in walls:
            state["positions"][command["actor"]] = target
        state["seq"] = command["seq"]
    state_valid(state)
    return state
