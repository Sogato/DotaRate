import os

EXCLUDE = {'.git', '.idea', 'venv', '__pycache__', '.venv', 'node_modules'}

def print_tree(path, prefix=''):
    entries = sorted(
        e for e in os.listdir(path)
        if e not in EXCLUDE
    )
    for i, entry in enumerate(entries):
        full = os.path.join(path, entry)
        is_last = (i == len(entries) - 1)
        connector = '└── ' if is_last else '├── '
        print(prefix + connector + entry)
        if os.path.isdir(full):
            extension = '    ' if is_last else '│   '
            print_tree(full, prefix + extension)

print_tree('.')  # текущая директория проекта
