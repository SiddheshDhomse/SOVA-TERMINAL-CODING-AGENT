# Basic Calculator

def addition(a, b):
    result = a + b
    print(f"Adding {a} + {b} = {result}")
    return result

def subtraction(a, b):
    result = a - b
    print(f"Subtracting {a} - {b} = {result}")
    return result

def multiplication(a, b):
    result = a * b
    print(f"Multiplying {a} * {b} = {result}")
    return result

def division(a, b):
    if b == 0:
        raise ValueError('Cannot divide by zero')
    result = a / b
    print(f"Dividing {a} / {b} = {result}")
    return result

def power(a, b):
    result = a ** b
    print(f"Power {a} ** {b} = {result}")
    return result

add = addition
subtract = subtraction
multiply = multiplication
divide = division
power = power
