# --- BEGIN COPYRIGHT BLOCK ---
# Copyright (C) 2016 Red Hat, Inc.
# All rights reserved.
#
# License: GPL (version 3 or any later version).
# See LICENSE for details.
# --- END COPYRIGHT BLOCK ---
#
import pytest
from lib389.tasks import *
from lib389.utils import *
from lib389.topologies import topology_m2

logging.getLogger(__name__).setLevel(logging.DEBUG)
log = logging.getLogger(__name__)

CONFIG_DN = 'cn=config'
ENCRYPTION_DN = 'cn=encryption,%s' % CONFIG_DN
RSA = 'RSA'
RSA_DN = 'cn=%s,%s' % (RSA, ENCRYPTION_DN)
ISSUER = 'cn=CAcert'
CACERT = 'CAcertificate'
M1SERVERCERT = 'Server-Cert1'
M2SERVERCERT = 'Server-Cert2'
M1LDAPSPORT = '41636'
M2LDAPSPORT = '42636'
M1SUBJECT = 'CN={},OU=389 Directory Server'.format(HOST_MASTER_1)
M2SUBJECT = 'CN={},OU=390 Directory Server'.format(HOST_MASTER_2)


@pytest.fixture(scope="module")
def add_entry(server, name, rdntmpl, start, num):
    log.info("\n######################### Adding %d entries to %s ######################" % (num, name))

    for i in range(num):
        ii = start + i
        dn = '%s%d,%s' % (rdntmpl, ii, DEFAULT_SUFFIX)
        try:
            server.add_s(Entry((dn, {'objectclass': 'top person extensibleObject'.split(),
                                     'uid': '%s%d' % (rdntmpl, ii),
                                     'cn': '%s user%d' % (name, ii),
                                     'sn': 'user%d' % (ii)})))
        except ldap.LDAPError as e:
            log.error('Failed to add %s ' % dn + e.message['desc'])
            assert False


def enable_ssl(server, ldapsport, mycert):
    log.info("\n######################### Enabling SSL LDAPSPORT %s ######################\n" % ldapsport)
    server.simple_bind_s(DN_DM, PASSWORD)
    server.modify_s(ENCRYPTION_DN, [(ldap.MOD_REPLACE, 'nsSSL3', 'off'),
                                    (ldap.MOD_REPLACE, 'nsTLS1', 'on'),
                                    (ldap.MOD_REPLACE, 'nsSSLClientAuth', 'allowed'),
                                    (ldap.MOD_REPLACE, 'nsSSL3Ciphers', '+all')])

    server.modify_s(CONFIG_DN, [(ldap.MOD_REPLACE, 'nsslapd-security', 'on'),
                                (ldap.MOD_REPLACE, 'nsslapd-ssl-check-hostname', 'off'),
                                (ldap.MOD_REPLACE, 'nsslapd-secureport', ldapsport)])

    server.add_s(Entry((RSA_DN, {'objectclass': "top nsEncryptionModule".split(),
                                 'cn': RSA,
                                 'nsSSLPersonalitySSL': mycert,
                                 'nsSSLToken': 'internal (software)',
                                 'nsSSLActivation': 'on'})))
    time.sleep(1)


def doAndPrintIt(cmdline, filename):
    proc = subprocess.Popen(cmdline, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if filename is None:
        log.info("      OUT:")
    else:
        log.info("      OUT: %s" % filename)
        fd = open(filename, "w")
    while True:
        l = proc.stdout.readline()
        if l == "":
            break
        if filename is None:
            log.info("      %s" % l)
        else:
            fd.write(l)
    log.info("      ERR:")
    while True:
        l = proc.stderr.readline()
        if l == "" or l == "\n":
            break
        log.info("      <%s>" % l)
        assert False

    if filename is not None:
        fd.close()
    time.sleep(1)


def create_keys_certs(topology_m2):
    log.info("\n######################### Creating SSL Keys and Certs ######################\n")

    global m1confdir
    m1confdir = topology_m2.ms["master1"].confdir
    global m2confdir
    m2confdir = topology_m2.ms["master2"].confdir

    log.info("##### shutdown master1")
    topology_m2.ms["master1"].stop(timeout=10)

    log.info("##### Creating a password file")
    pwdfile = '%s/pwdfile.txt' % (m1confdir)
    os.system('rm -f %s' % pwdfile)
    opasswd = os.popen("(ps -ef ; w ) | sha1sum | awk '{print $1}'", "r")
    passwd = opasswd.readline()
    pwdfd = open(pwdfile, "w")
    pwdfd.write(passwd)
    pwdfd.close()
    time.sleep(1)

    log.info("##### create the pin file")
    m1pinfile = '%s/pin.txt' % (m1confdir)
    m2pinfile = '%s/pin.txt' % (m2confdir)
    os.system('rm -f %s' % m1pinfile)
    os.system('rm -f %s' % m2pinfile)
    pintxt = 'Internal (Software) Token:%s' % passwd
    pinfd = open(m1pinfile, "w")
    pinfd.write(pintxt)
    pinfd.close()
    os.system('chmod 400 %s' % m1pinfile)

    log.info("##### Creating a noise file")
    noisefile = '%s/noise.txt' % (m1confdir)
    noise = os.popen("(w ; ps -ef ; date ) | sha1sum | awk '{print $1}'", "r")
    noisewdfd = open(noisefile, "w")
    noisewdfd.write(noise.readline())
    noisewdfd.close()
    time.sleep(1)

    cmdline = ['certutil', '-N', '-d', m1confdir, '-f', pwdfile]
    log.info("##### Create key3.db and cert8.db database (master1): %s" % cmdline)
    doAndPrintIt(cmdline, None)

    cmdline = ['certutil', '-G', '-d', m1confdir, '-z', noisefile, '-f', pwdfile]
    log.info("##### Creating encryption key for CA (master1): %s" % cmdline)
    # os.system('certutil -G -d %s -z %s -f %s' % (m1confdir, noisefile, pwdfile))
    doAndPrintIt(cmdline, None)

    time.sleep(2)

    log.info("##### Creating self-signed CA certificate (master1) -- nickname %s" % CACERT)
    os.system(
        '( echo y ; echo ; echo y ) | certutil -S -n "%s" -s "%s" -x -t "CT,," -m 1000 -v 120 -d %s -z %s -f %s -2' % (
        CACERT, ISSUER, m1confdir, noisefile, pwdfile))

    global M1SUBJECT
    cmdline = ['certutil', '-S', '-n', M1SERVERCERT, '-s', M1SUBJECT, '-c', CACERT, '-t', ',,', '-m', '1001', '-v',
               '120', '-d', m1confdir, '-z', noisefile, '-f', pwdfile]
    log.info("##### Creating Server certificate -- nickname %s: %s" % (M1SERVERCERT, cmdline))
    doAndPrintIt(cmdline, None)

    time.sleep(2)

    global M2SUBJECT
    cmdline = ['certutil', '-S', '-n', M2SERVERCERT, '-s', M2SUBJECT, '-c', CACERT, '-t', ',,', '-m', '1002', '-v',
               '120', '-d', m1confdir, '-z', noisefile, '-f', pwdfile]
    log.info("##### Creating Server certificate -- nickname %s: %s" % (M2SERVERCERT, cmdline))
    doAndPrintIt(cmdline, None)

    time.sleep(2)

    log.info("##### start master1")
    topology_m2.ms["master1"].start(timeout=10)

    log.info("##### enable SSL in master1 with all ciphers")
    enable_ssl(topology_m2.ms["master1"], M1LDAPSPORT, M1SERVERCERT)

    cmdline = ['certutil', '-L', '-d', m1confdir]
    log.info("##### Check the cert db: %s" % cmdline)
    doAndPrintIt(cmdline, None)

    log.info("##### stop master[12]")
    topology_m2.ms["master1"].stop(timeout=10)
    topology_m2.ms["master2"].stop(timeout=10)

    global mytmp
    mytmp = '/tmp'
    m2pk12file = '%s/%s.pk12' % (mytmp, M2SERVERCERT)
    cmd = 'pk12util -o %s -n "%s" -d %s -w %s -k %s' % (m2pk12file, M2SERVERCERT, m1confdir, pwdfile, pwdfile)
    log.info("##### Extract PK12 file for master2: %s" % cmd)
    os.system(cmd)
    time.sleep(1)

    log.info("##### Check PK12 files")
    if os.path.isfile(m2pk12file):
        log.info('%s is successfully extracted.' % m2pk12file)
    else:
        log.fatal('%s was not extracted.' % m2pk12file)
        assert False

    log.info("##### Initialize Cert DB for master2")
    cmdline = ['certutil', '-N', '-d', m2confdir, '-f', pwdfile]
    log.info("##### Create key3.db and cert8.db database (master2): %s" % cmdline)
    doAndPrintIt(cmdline, None)

    log.info("##### Import certs to master2")
    log.info('Importing %s' % CACERT)
    cacert = '%s%s.pem' % (mytmp, CACERT)
    cmdline = ['certutil', '-L', '-n', CACERT, '-d', m1confdir, '-a']
    doAndPrintIt(cmdline, cacert)

    os.system('certutil -A -n "%s" -t "CT,," -f %s -d %s -a -i %s' % (CACERT, pwdfile, m2confdir, cacert))
    cmd = 'pk12util -i %s -n "%s" -d %s -w %s -k %s' % (m2pk12file, M2SERVERCERT, m2confdir, pwdfile, pwdfile)
    log.info('##### Importing %s to master2: %s' % (M2SERVERCERT, cmd))
    os.system(cmd)
    log.info('copy %s to %s' % (m1pinfile, m2pinfile))
    os.system('cp %s %s' % (m1pinfile, m2pinfile))
    os.system('chmod 400 %s' % m2pinfile)
    time.sleep(1)

    log.info("##### start master2")
    topology_m2.ms["master2"].start(timeout=10)

    log.info("##### enable SSL in master2 with all ciphers")
    enable_ssl(topology_m2.ms["master2"], M2LDAPSPORT, M2SERVERCERT)

    log.info("##### restart master2")
    topology_m2.ms["master2"].restart(timeout=30)

    log.info("##### restart master1")
    topology_m2.ms["master1"].restart(timeout=30)

    log.info("\n######################### Creating SSL Keys and Certs Done ######################\n")


def config_tls_agreements(topology_m2):
    log.info("######################### Configure SSL/TLS agreements ######################")
    log.info("######################## master1 <-- startTLS -> master2 #####################")

    log.info("##### Update the agreement of master1")
    m1_m2_agmt = topology_m2.ms["master1_agmts"]["m1_m2"]
    topology_m2.ms["master1"].modify_s(m1_m2_agmt, [(ldap.MOD_REPLACE, 'nsDS5ReplicaTransportInfo', 'TLS')])

    log.info("##### Update the agreement of master2")
    m2_m1_agmt = topology_m2.ms["master2_agmts"]["m2_m1"]
    topology_m2.ms["master2"].modify_s(m2_m1_agmt, [(ldap.MOD_REPLACE, 'nsDS5ReplicaTransportInfo', 'TLS')])

    time.sleep(1)

    topology_m2.ms["master1"].restart(10)
    topology_m2.ms["master2"].restart(10)

    log.info("\n######################### Configure SSL/TLS agreements Done ######################\n")


def set_ssl_Version(server, name, version):
    log.info("\n######################### Set %s on %s ######################\n" %
             (version, name))
    server.simple_bind_s(DN_DM, PASSWORD)
    if version.startswith('SSL'):
        server.modify_s(ENCRYPTION_DN, [(ldap.MOD_REPLACE, 'nsSSL3', 'on'),
                                        (ldap.MOD_REPLACE, 'nsTLS1', 'off'),
                                        (ldap.MOD_REPLACE, 'sslVersionMin', 'SSL3'),
                                        (ldap.MOD_REPLACE, 'sslVersionMax', 'SSL3')])
    elif version.startswith('TLS'):
        server.modify_s(ENCRYPTION_DN, [(ldap.MOD_REPLACE, 'nsSSL3', 'off'),
                                        (ldap.MOD_REPLACE, 'nsTLS1', 'on'),
                                        (ldap.MOD_REPLACE, 'sslVersionMin', version),
                                        (ldap.MOD_REPLACE, 'sslVersionMax', version)])
    else:
        log.info("Invalid version %s", version)
        assert False


def test_ticket48784(topology_m2):
    """
    Set up 2way MMR:
        master_1 <----- startTLS -----> master_2

    Make sure the replication is working.
    Then, stop the servers and set only SSLv3 on master_1 while TLS1.2 on master_2
    Replication is supposed to fail.
    """
    log.info("Ticket 48784 - Allow usage of OpenLDAP libraries that don't use NSS for crypto")

    create_keys_certs(topology_m2)
    config_tls_agreements(topology_m2)

    add_entry(topology_m2.ms["master1"], 'master1', 'uid=m1user', 0, 5)
    add_entry(topology_m2.ms["master2"], 'master2', 'uid=m2user', 0, 5)

    time.sleep(10)

    log.info('##### Searching for entries on master1...')
    entries = topology_m2.ms["master1"].search_s(DEFAULT_SUFFIX, ldap.SCOPE_SUBTREE, '(uid=*)')
    assert 10 == len(entries)

    log.info('##### Searching for entries on master2...')
    entries = topology_m2.ms["master2"].search_s(DEFAULT_SUFFIX, ldap.SCOPE_SUBTREE, '(uid=*)')
    assert 10 == len(entries)

    log.info("##### openldap client just accepts sslVersionMin not Max.")
    set_ssl_Version(topology_m2.ms["master1"], 'master1', 'SSL3')
    set_ssl_Version(topology_m2.ms["master2"], 'master2', 'TLS1.2')

    log.info("##### restart master[12]")
    topology_m2.ms["master1"].restart(timeout=10)
    topology_m2.ms["master2"].restart(timeout=10)

    log.info("##### replication from master_1 to master_2 should be ok.")
    add_entry(topology_m2.ms["master1"], 'master1', 'uid=m1user', 10, 1)
    log.info("##### replication from master_2 to master_1 should fail.")
    add_entry(topology_m2.ms["master2"], 'master2', 'uid=m2user', 10, 1)

    time.sleep(10)

    log.info('##### Searching for entries on master1...')
    entries = topology_m2.ms["master1"].search_s(DEFAULT_SUFFIX, ldap.SCOPE_SUBTREE, '(uid=*)')
    assert 11 == len(entries)  # This is supposed to be "1" less than master 2's entry count

    log.info('##### Searching for entries on master2...')
    entries = topology_m2.ms["master2"].search_s(DEFAULT_SUFFIX, ldap.SCOPE_SUBTREE, '(uid=*)')
    assert 12 == len(entries)

    log.info("Ticket 48784 - PASSED")


if __name__ == '__main__':
    # Run isolated
    # -s for DEBUG mode

    CURRENT_FILE = os.path.realpath(__file__)
    pytest.main("-s %s" % CURRENT_FILE)
