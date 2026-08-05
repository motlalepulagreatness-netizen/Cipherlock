import stdio
import sys


class Base:
    def __init__(self):
        self._code = None
        self._cipher = None
        self._drawings = ['●', '○', '↓', '─', '│', '┌',
                          '┐', '└', '┘', '├', '┤', '┬', '┴', '┼']

    def run(self):
        stdio.writeln("Enter the true-passcode and true-cipher:")
        line = stdio.readLine()
        stdio.writeln(self._drawings[1]*4 + " " + "123456")
        stdio.writeln(self._drawings[2]*4 + " " + self._drawings[2]*6)
        self.validate_input(line)

    def validate_input(self, line):
        if line != "":
            if line[0] == " " or line[len(line)-1] == " ":
                stdio.writeln("Too many whitespaces detected.")
                quit()
            a = line.find(" ")
            if a == -1:
                stdio.writeln("Expected 2 input tokens.")
                quit()
            if self.check_code(line[:a]) and self.check_cipher(line[a+1:]):
                stdio.writeln("Right")

    def check_code(self, code):
        if len(code) != 4:
            stdio.writeln("A code must be 4 digits.")
            quit()
        for k in code:
            if k not in ["1", "2", "3", "4", "5", "6"]:
                stdio.writeln("A code must contain only digits 1-6")
                quit()
        self._code = int(code)
        return True

    def check_cipher(self, cipher):
        if len(cipher) != 6:
            stdio.writeln("A cipher must be 6 digits.")
            quit()
        data = []
        for k in cipher:
            if k not in ["1", "2", "3", "4", "5", "6"]:
                stdio.writeln("A cipher must contain only digits 1-6")
                quit()
            if k in data:
                stdio.writeln("No digit may be repeated in a cipher.")
                quit()
            data += [k]
        self._cipher = int(cipher)
        return True
