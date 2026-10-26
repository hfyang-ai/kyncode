def make_change(coins, amount):
    coins = sorted(coins, reverse=True)
    result = []
    for c in coins:
        while amount >= c:
            result.append(c)
            amount -= c
    return result
