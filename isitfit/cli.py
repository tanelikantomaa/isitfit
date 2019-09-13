# RuntimeError: Click will abort further execution because Python 3 was configured to use ASCII as encoding for the environment. 
# Consult https://click.palletsprojects.com/en/7.x/python3/ for mitigation steps.
from gitRemoteAws.utils import mysetlocale
mysetlocale()


import logging
logger = logging.getLogger('isitfit')

from .mainManager import MainManager
from .utilizationListener import UtilizationListener
import click

from . import isitfit_version

@click.command()
@click.option('--debug', is_flag=True)
@click.option('--version', is_flag=True)
def cli(debug, version):

    if version:
      print('isitfit version %s'%isitfit_version)
      return

    logLevel = logging.DEBUG if debug else logging.INFO
    ch = logging.StreamHandler()
    ch.setLevel(logLevel)
    logger.addHandler(ch)
    logger.setLevel(logLevel)


    logger.info("Is it fit?")

    logger.info("Initializing")
    ul = UtilizationListener()
    mm = MainManager()

    # utilization listeners
    mm.add_listener('ec2', ul.per_ec2)
    mm.add_listener('all', ul.after_all)

    # start download data and processing
    logger.info("Fetching history...")
    mm.get_ifi()

    logger.info("... done")
    logger.info("* isitfit version %s is based on CPU utilization only (and not yet on memory utilization)"%isitfit_version)


if __name__ == '__main__':
  cli()
