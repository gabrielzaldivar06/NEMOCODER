def fibonacci(n):
    """
    Devuelve la secuencia de Fibonacci con los primeros n números.
    """
    if n <= 0:
        return []
    if n == 1:
        return [0]
    elif n == 2:
        return [0, 1]
    
    sequence = [0, 1]
    while len(sequence) < n:
        next_fib = sequence[-1] + sequence[-2]
        sequence.append(next_fib)
    
    return sequence
