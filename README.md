This folder contains the implementation of the infrastructure scripts. The contents are
generally not meant for modification within repos these scripts are pulled into, unlike
any files in the parent directory (changes in this folder can be far reaching so can
be difficult to merge on subsequent pulls if changes are made, whereas changes to the
parent folder will be kept minimal).

The main parts of this folder are as follows:

* `run.sh` - script invoked by the stubs in `../scripts/` that executes the corresponding
           scripts in this directory inside docker.
* `Dockerfile` - used to create the image the container to run this script is based on.
* `release.py`, `deploy.py` - implementation of the commands.
* `test`, `test*.py` - tests (also run in the container).
* `util.py` - code shared between commands.
* `requirements.txt` - python/pip dependencies.

