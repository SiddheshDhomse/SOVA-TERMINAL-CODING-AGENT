# build by SOVA
import tkinter as tk

# Tkinter-based simple calculator GUI
class Calculator:
    def __init__(self):
        # Initialise the main application window
        self.window = tk.Tk()
        self.window.title("Calculator")

        # Configure window background colour
        self.window.configure(bg='#2b2b2b')

        # Create the display entry widget for numbers and operations
        self.entry_field = tk.Entry(
            self.window,
            width=20,
            font=('Arial', 24),
            borderwidth=5,
            relief='ridge',
            justify='right',
            bg='#1e1e1e',
            fg='white',
            insertbackground='white'
        )
        self.entry_field.grid(row=0, column=0, columnspan=4, padx=10, pady=10, sticky='nsew')

        # Create numeric buttons (0-9) and decimal point
        buttons = {}
        for txt in ['7', '8', '9', '4', '5', '6', '1', '2', '3', '0', '.']:
            btn = tk.Button(
                self.window,
                text=txt,
                font=('Arial', 18),
                bg='#ffffff',
                fg='black',
                activebackground='#e0e0e0',
                width=4,
                height=2
            )
            buttons[txt] = btn

        # Create operation buttons with distinct colours
        button_add = tk.Button(self.window, text='+', font=('Arial', 18), bg='#ff9500', fg='white', activebackground='#e08900', width=4, height=2)
        button_subtract = tk.Button(self.window, text='-', font=('Arial', 18), bg='#ff9500', fg='white', activebackground='#e08900', width=4, height=2)
        button_multiply = tk.Button(self.window, text='*', font=('Arial', 18), bg='#ff9500', fg='white', activebackground='#e08900', width=4, height=2)
        button_divide = tk.Button(self.window, text='/', font=('Arial', 18), bg='#ff9500', fg='white', activebackground='#e08900', width=4, height=2)
        button_equals = tk.Button(self.window, text="=", font=('Arial', 18), bg='#34c759', fg='white', activebackground='#28a745', width=9, height=2)
        button_clear = tk.Button(self.window, text="C", font=('Arial', 18), bg='#ff3b30', fg='white', activebackground='#d32f2f', width=9, height=2)

        # Position numeric buttons in a grid layout (3 columns)
        layout = [
            ('7', 1, 0), ('8', 1, 1), ('9', 1, 2),
            ('4', 2, 0), ('5', 2, 1), ('6', 2, 2),
            ('1', 3, 0), ('2', 3, 1), ('3', 3, 2),
            ('0', 4, 0), ('.', 4, 1),
        ]
        for txt, r, c in layout:
            buttons[txt].grid(row=r, column=c, padx=5, pady=5, sticky='nsew')

        # Position operation buttons on the right side of the grid
        button_add.grid(row=1, column=3, padx=5, pady=5, sticky='nsew')
        button_subtract.grid(row=2, column=3, padx=5, pady=5, sticky='nsew')
        button_multiply.grid(row=3, column=3, padx=5, pady=5, sticky='nsew')
        button_divide.grid(row=4, column=3, padx=5, pady=5, sticky='nsew')
        button_equals.grid(row=5, column=2, columnspan=2, padx=5, pady=5, sticky='nsew')
        button_clear.grid(row=5, column=0, columnspan=2, padx=5, pady=5, sticky='nsew')

        # Callback for button presses
        def click_button(number):
            current = self.entry_field.get()
            if number == "=":
                try:
                    # Evaluate the expression entered by the user
                    result = eval(current)
                    self.entry_field.delete(0, tk.END)
                    self.entry_field.insert(tk.END, str(result))
                except Exception:
                    # Show error message on evaluation failure
                    self.entry_field.delete(0, tk.END)
                    self.entry_field.insert(tk.END, "Error")
            else:
                # Append the pressed button's value to the display
                self.entry_field.insert(tk.END, number)

        # Callback to clear the display
        def clear_entry():
            self.entry_field.delete(0, tk.END)

        # Bind numeric buttons to the click handler
        for txt, btn in buttons.items():
            btn.config(command=lambda t=txt: click_button(t))
        # Bind operation and control buttons
        button_add.config(command=lambda: click_button('+'))
        button_subtract.config(command=lambda: click_button('-'))
        button_multiply.config(command=lambda: click_button('*'))
        button_divide.config(command=lambda: click_button('/'))
        button_equals.config(command=lambda: click_button('='))
        button_clear.config(command=clear_entry)

        # Start the Tkinter event loop
        self.window.mainloop()

# Execute the calculator UI when the script is run directly
if __name__ == "__main__":
    Calculator()
