from order_state import can_transition


def advance(state, target):
    if can_transition(state, target):
        return target
    return state


def cancel(state):
    if state != 'delivered':
        return 'cancel'
    return state
