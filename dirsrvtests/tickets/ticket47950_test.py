import os
import sys
import time
import ldap
import logging
import socket
import pytest
from lib389 import DirSrv, Entry, tools, tasks
from lib389.tools import DirSrvTools
from lib389._constants import *
from lib389.properties import *
from lib389.tasks import *
from constants import *

log = logging.getLogger(__name__)

installation_prefix = None

USER1_DN = "uid=user1,%s" % DEFAULT_SUFFIX
USER2_DN = "uid=user2,%s" % DEFAULT_SUFFIX


class TopologyStandalone(object):
    def __init__(self, standalone):
        standalone.open()
        self.standalone = standalone


@pytest.fixture(scope="module")
def topology(request):
    '''
        This fixture is used to standalone topology for the 'module'.
        At the beginning, It may exists a standalone instance.
        It may also exists a backup for the standalone instance.

        Principle:
            If standalone instance exists:
                restart it
            If backup of standalone exists:
                create/rebind to standalone

                restore standalone instance from backup
            else:
                Cleanup everything
                    remove instance
                    remove backup
                Create instance
                Create backup
    '''
    global installation_prefix

    if installation_prefix:
        args_instance[SER_DEPLOYED_DIR] = installation_prefix

    standalone = DirSrv(verbose=False)

    # Args for the standalone instance
    args_instance[SER_HOST] = HOST_STANDALONE
    args_instance[SER_PORT] = PORT_STANDALONE
    args_instance[SER_SERVERID_PROP] = SERVERID_STANDALONE
    args_standalone = args_instance.copy()
    standalone.allocate(args_standalone)

    # Get the status of the backups
    backup_standalone = standalone.checkBackupFS()

    # Get the status of the instance and restart it if it exists
    instance_standalone = standalone.exists()
    if instance_standalone:
        # assuming the instance is already stopped, just wait 5 sec max
        standalone.stop(timeout=5)
        standalone.start(timeout=10)

    if backup_standalone:
        # The backup exist, assuming it is correct
        # we just re-init the instance with it
        if not instance_standalone:
            standalone.create()
            # Used to retrieve configuration information (dbdir, confdir...)
            standalone.open()

        # restore standalone instance from backup
        standalone.stop(timeout=10)
        standalone.restoreFS(backup_standalone)
        standalone.start(timeout=10)

    else:
        # We should be here only in two conditions
        #      - This is the first time a test involve standalone instance
        #      - Something weird happened (instance/backup destroyed)
        #        so we discard everything and recreate all

        # Remove the backup. So even if we have a specific backup file
        # (e.g backup_standalone) we clear backup that an instance may have created
        if backup_standalone:
            standalone.clearBackupFS()

        # Remove the instance
        if instance_standalone:
            standalone.delete()

        # Create the instance
        standalone.create()

        # Used to retrieve configuration information (dbdir, confdir...)
        standalone.open()

        # Time to create the backups
        standalone.stop(timeout=10)
        standalone.backupfile = standalone.backupFS()
        standalone.start(timeout=10)

    # clear the tmp directory
    standalone.clearTmpDir(__file__)

    #
    # Here we have standalone instance up and running
    # Either coming from a backup recovery
    # or from a fresh (re)init
    # Time to return the topology
    return TopologyStandalone(standalone)


def test_ticket47950(topology):
    """
        Testing nsslapd-plugin-binddn-tracking does not cause issues around
        access control and reconfiguring replication/repl agmt.
    """

    log.info('Testing Ticket 47950 - Testing nsslapd-plugin-binddn-tracking')

    #
    # Turn on bind dn tracking
    #
    try:
        topology.standalone.modify_s("cn=config", [(ldap.MOD_REPLACE, 'nsslapd-plugin-binddn-tracking', 'on')])
        log.info('nsslapd-plugin-binddn-tracking enabled.')
    except ldap.LDAPError, e:
        log.error('Failed to enable bind dn tracking: ' + e.message['desc'])
        assert False

    #
    # Add two users
    #
    try:
        topology.standalone.add_s(Entry((USER1_DN, {
                                        'objectclass': "top person inetuser".split(),
                                        'userpassword': "password",
                                        'sn': "1",
                                        'cn': "user 1"})))
        log.info('Added test user %s' % USER1_DN)
    except ldap.LDAPError, e:
        log.error('Failed to add %s: %s' % (USER1_DN, e.message['desc']))
        assert False

    try:
        topology.standalone.add_s(Entry((USER2_DN, {
                                        'objectclass': "top person inetuser".split(),
                                        'sn': "2",
                                        'cn': "user 2"})))
        log.info('Added test user %s' % USER2_DN)
    except ldap.LDAPError, e:
        log.error('Failed to add user1: ' + e.message['desc'])
        assert False

    #
    # Add an aci
    #
    try:
        acival = '(targetattr ="cn")(version 3.0;acl "Test bind dn tracking"' + \
             ';allow (all) (userdn = "ldap:///%s");)' % USER1_DN

        topology.standalone.modify_s(DEFAULT_SUFFIX, [(ldap.MOD_ADD, 'aci', acival)])
        log.info('Added aci')
    except ldap.LDAPError, e:
        log.error('Failed to add aci: ' + e.message['desc'])
        assert False

    #
    # Make modification as user
    #
    try:
        topology.standalone.simple_bind_s(USER1_DN, "password")
        log.info('Bind as user %s successful' % USER1_DN)
    except ldap.LDAPError, e:
        log.error('Failed to bind as user1: ' + e.message['desc'])
        assert False

    try:
        topology.standalone.modify_s(USER2_DN, [(ldap.MOD_REPLACE, 'cn', 'new value')])
        log.info('%s successfully modified user %s' % (USER1_DN, USER2_DN))
    except ldap.LDAPError, e:
        log.error('Failed to update user2: ' + e.message['desc'])
        assert False

    #
    # Setup replica and create a repl agmt
    #
    try:
        topology.standalone.simple_bind_s(DN_DM, PASSWORD)
        log.info('Bind as %s successful' % DN_DM)
    except ldap.LDAPError, e:
        log.error('Failed to bind as rootDN: ' + e.message['desc'])
        assert False

    try:
        topology.standalone.replica.enableReplication(suffix=DEFAULT_SUFFIX, role=REPLICAROLE_MASTER,
                                                  replicaId=REPLICAID_MASTER)
        log.info('Successfully enabled replication.')
    except ValueError:
        log.error('Failed to enable replication')
        assert False

    properties = {RA_NAME: r'test plugin internal bind dn',
                  RA_BINDDN: defaultProperties[REPLICATION_BIND_DN],
                  RA_BINDPW: defaultProperties[REPLICATION_BIND_PW],
                  RA_METHOD: defaultProperties[REPLICATION_BIND_METHOD],
                  RA_TRANSPORT_PROT: defaultProperties[REPLICATION_TRANSPORT]}

    try:
        repl_agreement = topology.standalone.agreement.create(suffix=DEFAULT_SUFFIX, host="127.0.0.1",
                                                          port="7777", properties=properties)
        log.info('Successfully created replication agreement')
    except InvalidArgumentError, e:
        log.error('Failed to create replication agreement: ' + e.message['desc'])
        assert False

    #
    # modify replica
    #
    try:
        properties = {REPLICA_ID: "7"}
        topology.standalone.replica.setProperties(DEFAULT_SUFFIX, None, None, properties)
        log.info('Successfully modified replica')
    except ldap.LDAPError, e:
        log.error('Failed to update replica config: ' + e.message['desc'])
        assert False

    #
    # modify repl agmt
    #
    try:
        properties = {RA_CONSUMER_PORT: "8888"}
        topology.standalone.agreement.setProperties(None, repl_agreement, None, properties)
        log.info('Successfully modified replication agreement')
    except ValueError:
        log.error('Failed to update replica agreement: ' + repl_agreement)
        assert False

    # We passed
    log.info("Test Passed.")


def test_ticket47953_final(topology):
    topology.standalone.delete()


def run_isolated():
    '''
        run_isolated is used to run these test cases independently of a test scheduler (xunit, py.test..)
        To run isolated without py.test, you need to
            - edit this file and comment '@pytest.fixture' line before 'topology' function.
            - set the installation prefix
            - run this program
    '''
    global installation_prefix
    installation_prefix = None

    topo = topology(True)
    test_ticket47950(topo)
    test_ticket47953_final(topo)


if __name__ == '__main__':
    run_isolated()