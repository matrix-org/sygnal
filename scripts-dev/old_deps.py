import os

"""
This script outputs a list of requirements, which, if fed to pip install -r,
will install the oldest versions of the dependencies specified by the
dependencies.py file in the repository root.
"""


here = os.path.abspath(os.path.dirname(__file__))


def read_file(path_segments):
    """Read a file from the package. Takes a list of strings to join to
    make the path"""
    file_path = os.path.join(here, *path_segments)
    with open(file_path) as f:
        return f.read()


def exec_file(path_segments):
    """Execute a single python file to get the variables defined in it"""
    result = {}
    code = read_file(path_segments)
    exec(code, result)
    return result


if __name__ == "__main__":
    dependencies = exec_file(("..", "dependencies.py"))
    for requirement in dependencies["INSTALL_REQUIRES"]:
        print(requirement.replace(">=", "=="))
