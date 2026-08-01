import sys
import stdio


def validate_input(arg):
    if len(arg) != 2:
        if len(arg) > 2:
            stdio.writeln("Too many arguments. Expected 2.")
        else:
            stdio.writeln("Too few arguments. Expected 2.")
        quit()
    else:

        if not (arg[0].isdigit()) or not (arg[0] in ['1', '0']):
            stdio.writeln(
                'The game type should be "0" for vanilla, or "1" for agentic.')
            quit()
        if not (arg[1].isdigit()) or not (arg[1] in ['0', '1', '2', '3']):
            stdio.write('The AI type should be "0" for no AI, ')
            stdio.write('"1" for the cipher guess strategy, ')
            stdio.write('"2" for the Vanilla guess strategy, ')
            stdio.writeln('or "3" for the Agentic guess strategy.')
            quit()
        game_type = int(arg[0])
        ai_type = int(arg[1])
        if game_type == 0 and ai_type == 3:
            stdio.writeln('AI type 3 can only be played for game type 1.')
            quit()


def main():
    args = sys.argv[1:]
    validate_input(args)


if __name__ == "__main__":
    main()
