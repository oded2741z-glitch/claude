"""Install everything the apps need into the Python that runs this file.

Run it the same way you run the app — double-click, IDLE, the VS Code run
button, whatever you use. Whichever Python opens this file is the one that
gets the packages, so they cannot end up in a different Python from the app.
"""

import subprocess
import sys

# keep in step with requirements.txt
PACKAGES = ["google-generativeai", "gTTS", "pygame", "pillow", "SpeechRecognition", "cryptography"]
OPTIONAL = ["pyaudio"]      # only dictation needs it, and it does not build on every Python


def pip_install(packages):
    return subprocess.call([sys.executable, "-m", "pip", "install", *packages])


def main():
    print("Installing into this Python:")
    print("   ", sys.executable)
    print()

    if pip_install(PACKAGES) != 0:
        print()
        print("Installation FAILED - the lines above say why. Nothing else was changed.")
        return 1

    print()
    if pip_install(OPTIONAL) != 0:
        print()
        print("Everything is installed except PyAudio. The app will run;")
        print("only the Dictate button and voice mode will not work.")
    else:
        print("Everything is installed, including PyAudio for dictation.")
    print()
    print("You can now run the app.")
    return 0


if __name__ == "__main__":
    code = main()
    input("\nPress Enter to close this window.")    # a double-clicked window would otherwise vanish
    sys.exit(code)
