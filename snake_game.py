# Simple Snake Game using the built-in turtle module
# Run this script with Python 3.x

import turtle
import time
import random


def run_game():
    """Run the Snake game.

    The game logic is encapsulated in this function so that importing this
    module does not start the interactive window automatically. This makes the
    module safe to import in environments (e.g., automated tests) where a GUI
    cannot be displayed.
    """
    # Screen setup
    wn = turtle.Screen()
    wn.title("Snake Game")
    wn.bgcolor("black")
    wn.setup(width=600, height=600)
    wn.tracer(0)  # Turns off the screen updates

    # Draw visible border
    border = turtle.Turtle()
    border.speed(0)
    border.color("white")
    border.penup()
    border.goto(-290, -290)
    border.pendown()
    for _ in range(4):
        border.forward(580)
        border.left(90)
    border.hideturtle()

    # Snake head
    head = turtle.Turtle()
    head.speed(0)
    head.shape("square")
    head.color("white")
    head.penup()
    head.goto(0, 0)
    head.direction = "stop"

    # Snake food
    food = turtle.Turtle()
    food.speed(0)
    food.shape("circle")
    food.color("red")
    food.penup()
    food.goto(0, 100)

    segments = []

    # Score
    score = 0
    high_score = 0

    # Score display
    pen = turtle.Turtle()
    pen.speed(0)
    pen.shape("square")
    pen.color("white")
    pen.penup()
    pen.hideturtle()
    pen.goto(0, 260)
    pen.write("Score: 0  High Score: 0", align="center", font=("Courier", 24, "normal"))

    # Game Over display
    game_over_pen = turtle.Turtle()
    game_over_pen.speed(0)
    game_over_pen.shape("square")
    game_over_pen.color("red")
    game_over_pen.penup()
    game_over_pen.hideturtle()

    # Functions for movement
    def go_up():
        if head.direction != "down":
            head.direction = "up"

    def go_down():
        if head.direction != "up":
            head.direction = "down"

    def go_left():
        if head.direction != "right":
            head.direction = "left"

    def go_right():
        if head.direction != "left":
            head.direction = "right"

    def show_game_over():
        game_over_pen.clear()
        game_over_pen.goto(0, 0)
        game_over_pen.write("Game Over", align="center", font=("Courier", 36, "bold"))

    # Move the snake head based on its direction
    def move():
        if head.direction == "up":
            y = head.ycor()
            head.sety(y + 20)
        if head.direction == "down":
            y = head.ycor()
            head.sety(y - 20)
        if head.direction == "left":
            x = head.xcor()
            head.setx(x - 20)
        if head.direction == "right":
            x = head.xcor()
            head.setx(x + 20)

    # Keyboard bindings
    wn.listen()
    wn.onkey(go_up, "w")
    wn.onkey(go_down, "s")
    wn.onkey(go_left, "a")
    wn.onkey(go_right, "d")
    wn.onkey(go_up, "Up")
    wn.onkey(go_down, "Down")
    wn.onkey(go_left, "Left")
    wn.onkey(go_right, "Right")

    # Main game loop
    while True:
        wn.update()

        # Check for a collision with the border
        if head.xcor() > 290 or head.xcor() < -290 or head.ycor() > 290 or head.ycor() < -290:
            # Collision with border – game over
            show_game_over()
            time.sleep(2)
            # Reset game state
            head.goto(0, 0)
            head.direction = "stop"
            # Hide the segments
            for segment in segments:
                segment.goto(1000, 1000)
            segments.clear()
            # Reset the score
            score = 0
            pen.clear()
            pen.write(f"Score: {score}  High Score: {high_score}", align="center", font=("Courier", 24, "normal"))
            # Clear game over message
            game_over_pen.clear()

        # Check for a collision with the food
        if head.distance(food) < 20:
            # Move the food to a random spot
            x = random.randint(-14, 14) * 20
            y = random.randint(-14, 14) * 20
            food.goto(x, y)

            # Add a segment
            new_segment = turtle.Turtle()
            new_segment.speed(0)
            new_segment.shape("square")
            new_segment.color("grey")
            new_segment.penup()
            segments.append(new_segment)

            # Increase the score
            score += 10
            if score > high_score:
                high_score = score
            pen.clear()
            pen.write(f"Score: {score}  High Score: {high_score}", align="center", font=("Courier", 24, "normal"))

        # Move the end segments first in reverse order
        for index in range(len(segments) - 1, 0, -1):
            x = segments[index - 1].xcor()
            y = segments[index - 1].ycor()
            segments[index].goto(x, y)

        # Move segment 0 to where the head is
        if len(segments) > 0:
            segments[0].goto(head.xcor(), head.ycor())

        move()

        # Check for head collision with the body segments
        for segment in segments:
            if segment.distance(head) < 20:
                time.sleep(1)
                head.goto(0, 0)
                head.direction = "stop"

                # Hide the segments
                for segment in segments:
                    segment.goto(1000, 1000)
                segments.clear()
                score = 0
                pen.clear()
                pen.write(f"Score: {score}  High Score: {high_score}", align="center", font=("Courier", 24, "normal"))
                break

        time.sleep(0.1)

    wn.mainloop()


if __name__ == "__main__":
    run_game()
