ALLOWED = {
    'pending': ['paid', 'cancel'],
    'paid': ['shipped', 'cancel'],
    'shipped': ['delivered'],
    'delivered': [],
    'cancel': [],
}


def can_transition(from_state, to_state):
    return to_state in ALLOWED.get(from_state, [])


def display(state):
    return state.upper()
