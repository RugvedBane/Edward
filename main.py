"""Compat entry: python main.py '<task>' -> edward wrap -- pi '<task>'"""
import sys

from edward.cli import main

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py '<task prompt>'\n(better: edward wrap -- pi '<task prompt>')")
        sys.exit(1)
    sys.exit(main(["wrap", "--", "pi", sys.argv[1]]))
