"""Tests for certbot._internal.client."""
import platform
import shutil
import tempfile
import unittest

from josepy import interfaces
try:
    import mock
except ImportError: # pragma: no cover
    from unittest import mock

from certbot import errors
from certbot import util
from certbot._internal import account
from certbot.compat import os
import certbot.tests.util as test_util


KEY = test_util.load_vector("rsa512_key.pem")
CSR_SAN = test_util.load_vector("csr-san_512.pem")

# pylint: disable=line-too-long

class DetermineUserAgentTest(test_util.ConfigTestCase):
    """Tests for certbot._internal.client.determine_user_agent."""

    def _call(self):
        from certbot._internal.client import determine_user_agent
        return determine_user_agent(self.config)

    @mock.patch.dict(os.environ, {"CERTBOT_DOCS": "1"})
    def test_docs_value(self):
        self._test(expect_doc_values=True)

    @mock.patch.dict(os.environ, {})
    def test_real_values(self):
        self._test(expect_doc_values=False)

    def _test(self, expect_doc_values):
        ua = self._call()

        if expect_doc_values:
            doc_value_check = self.assertIn
            real_value_check = self.assertNotIn
        else:
            doc_value_check = self.assertNotIn
            real_value_check = self.assertIn

        doc_value_check("OS_NAME OS_VERSION", ua)
        doc_value_check("major.minor.patchlevel", ua)
        real_value_check(util.get_os_info_ua(), ua)
        real_value_check(platform.python_version(), ua)


class RegisterTest(test_util.ConfigTestCase):
    """Tests for certbot._internal.client.register."""

    def setUp(self):
        super().setUp()
        self.config.rsa_key_size = 1024
        self.config.register_unsafely_without_email = False
        self.config.email = "alias@example.com"
        self.account_storage = account.AccountMemoryStorage()

    def _call(self):
        from certbot._internal.client import register
        tos_cb = mock.MagicMock()
        return register(self.config, self.account_storage, tos_cb)

    @staticmethod
    def _public_key_mock():
        m = mock.Mock(__class__=interfaces.JSONDeSerializable)
        m.to_partial_json.return_value = '{"a": 1}'
        return m

    @staticmethod
    def _new_acct_dir_mock():
        return "/acme/new-account"

    @staticmethod
    def _true_mock():
        return True

    @staticmethod
    def _false_mock():
        return False

    def test_no_tos(self):
        with mock.patch("certbot._internal.client.acme_client.BackwardsCompatibleClientV2") as mock_client:
            mock_client.new_account_and_tos().terms_of_service = "http://tos"
            mock_client().external_account_required.side_effect = self._false_mock
            with mock.patch("certbot._internal.eff.prepare_subscription") as mock_prepare:
                mock_client().new_account_and_tos.side_effect = errors.Error
                self.assertRaises(errors.Error, self._call)
                self.assertIs(mock_prepare.called, False)

                mock_client().new_account_and_tos.side_effect = None
                self._call()
                self.assertIs(mock_prepare.called, True)

    def test_it(self):
        with mock.patch("certbot._internal.client.acme_client.BackwardsCompatibleClientV2") as mock_client:
            mock_client().external_account_required.side_effect = self._false_mock
            with mock.patch("certbot._internal.eff.handle_subscription"):
                self._call()

    @mock.patch("certbot._internal.client.display_ops.get_email")
    def test_email_retry(self, mock_get_email):
        from acme import messages
        self.config.noninteractive_mode = False
        msg = "DNS problem: NXDOMAIN looking up MX for example.com"
        mx_err = messages.Error.with_code('invalidContact', detail=msg)
        with mock.patch("certbot._internal.client.acme_client.BackwardsCompatibleClientV2") as mock_client:
            mock_client().external_account_required.side_effect = self._false_mock
            with mock.patch("certbot._internal.eff.prepare_subscription") as mock_prepare:
                mock_client().new_account_and_tos.side_effect = [mx_err, mock.MagicMock()]
                self._call()
                self.assertEqual(mock_get_email.call_count, 1)
                self.assertIs(mock_prepare.called, True)

    def test_email_invalid_noninteractive(self):
        from acme import messages
        self.config.noninteractive_mode = True
        msg = "DNS problem: NXDOMAIN looking up MX for example.com"
        mx_err = messages.Error.with_code('invalidContact', detail=msg)
        with mock.patch("certbot._internal.client.acme_client.BackwardsCompatibleClientV2") as mock_client:
            mock_client().external_account_required.side_effect = self._false_mock
            with mock.patch("certbot._internal.eff.handle_subscription"):
                mock_client().new_account_and_tos.side_effect = [mx_err, mock.MagicMock()]
                self.assertRaises(errors.Error, self._call)

    def test_needs_email(self):
        self.config.email = None
        self.assertRaises(errors.Error, self._call)

    @mock.patch("certbot._internal.client.logger")
    def test_without_email(self, mock_logger):
        with mock.patch("certbot._internal.eff.prepare_subscription") as mock_prepare:
            with mock.patch("certbot._internal.client.acme_client.BackwardsCompatibleClientV2") as mock_clnt:
                mock_clnt().external_account_required.side_effect = self._false_mock
                self.config.email = None
                self.config.register_unsafely_without_email = True
                self.config.dry_run = False
                self._call()
                mock_logger.debug.assert_called_once_with(mock.ANY)
                self.assertIs(mock_prepare.called, True)

    @mock.patch("certbot._internal.client.display_ops.get_email")
    def test_dry_run_no_staging_account(self, mock_get_email):
        """Tests dry-run for no staging account, expect account created with no email"""
        with mock.patch("certbot._internal.client.acme_client.BackwardsCompatibleClientV2") as mock_client:
            mock_client().external_account_required.side_effect = self._false_mock
            with mock.patch("certbot._internal.eff.handle_subscription"):
                self.config.dry_run = True
                self._call()
                # check Certbot did not ask the user to provide an email
                self.assertIs(mock_get_email.called, False)
                # check Certbot created an account with no email. Contact should return empty
                self.assertFalse(mock_client().new_account_and_tos.call_args[0][0].contact)

    def test_with_eab_arguments(self):
        with mock.patch("certbot._internal.client.acme_client.BackwardsCompatibleClientV2") as mock_client:
            mock_client().client.directory.__getitem__ = mock.Mock(
                side_effect=self._new_acct_dir_mock
            )
            mock_client().external_account_required.side_effect = self._false_mock
            with mock.patch("certbot._internal.eff.handle_subscription"):
                target = "certbot._internal.client.messages.ExternalAccountBinding.from_data"
                with mock.patch(target) as mock_eab_from_data:
                    self.config.eab_kid = "test-kid"
                    self.config.eab_hmac_key = "J2OAqW4MHXsrHVa_PVg0Y-L_R4SYw0_aL1le6mfblbE"
                    self._call()

                    self.assertIs(mock_eab_from_data.called, True)

    def test_without_eab_arguments(self):
        with mock.patch("certbot._internal.client.acme_client.BackwardsCompatibleClientV2") as mock_client:
            mock_client().external_account_required.side_effect = self._false_mock
            with mock.patch("certbot._internal.eff.handle_subscription"):
                target = "certbot._internal.client.messages.ExternalAccountBinding.from_data"
                with mock.patch(target) as mock_eab_from_data:
                    self.config.eab_kid = None
                    self.config.eab_hmac_key = None
                    self._call()

                    self.assertIs(mock_eab_from_data.called, False)

    def test_external_account_required_without_eab_arguments(self):
        with mock.patch("certbot._internal.client.acme_client.BackwardsCompatibleClientV2") as mock_client:
            mock_client().client.net.key.public_key = mock.Mock(side_effect=self._public_key_mock)
            mock_client().external_account_required.side_effect = self._true_mock
            with mock.patch("certbot._internal.eff.handle_subscription"):
                with mock.patch("certbot._internal.client.messages.ExternalAccountBinding.from_data"):
                    self.config.eab_kid = None
                    self.config.eab_hmac_key = None

                    self.assertRaises(errors.Error, self._call)

    def test_unsupported_error(self):
        from acme import messages
        msg = "Test"
        mx_err = messages.Error.with_code("malformed", detail=msg, title="title")
        with mock.patch("certbot._internal.client.acme_client.BackwardsCompatibleClientV2") as mock_client:
            mock_client().client.directory.__getitem__ = mock.Mock(
                side_effect=self._new_acct_dir_mock
            )
            mock_client().external_account_required.side_effect = self._false_mock
            with mock.patch("certbot._internal.eff.handle_subscription") as mock_handle:
                mock_client().new_account_and_tos.side_effect = [mx_err, mock.MagicMock()]
                self.assertRaises(messages.Error, self._call)
        self.assertIs(mock_handle.called, False)


class ClientTestCommon(test_util.ConfigTestCase):
    """Common base class for certbot._internal.client.Client tests."""

    def setUp(self):
        super().setUp()
        self.config.no_verify_ssl = False
        self.config.allow_subset_of_names = False

        self.account = mock.MagicMock(**{"key.pem": KEY})

        from certbot._internal.client import Client
        with mock.patch("certbot._internal.client.acme_client.BackwardsCompatibleClientV2") as acme:
            self.acme_client = acme
            self.acme = acme.return_value = mock.MagicMock()
            self.client = Client(
                config=self.config, account_=self.account,
                auth=None, installer=None)


class ClientTest(ClientTestCommon):
    """Tests for certbot._internal.client.Client."""

    def setUp(self):
        super().setUp()

        self.config.allow_subset_of_names = False
        self.config.dry_run = False
        self.eg_domains = ["example.com", "www.example.com"]
        self.eg_order = mock.MagicMock(
            authorizations=[None],
            csr_pem=mock.sentinel.csr_pem)

    def test_init_acme_verify_ssl(self):
        net = self.acme_client.call_args[0][0]
        self.assertIs(net.verify_ssl, True)

    def _mock_obtain_certificate(self):
        self.client.auth_handler = mock.MagicMock()
        self.client.auth_handler.handle_authorizations.return_value = [None]
        self.client.auth_handler.deactivate_valid_authorizations.return_value = ([], [])
        self.acme.finalize_order.return_value = self.eg_order
        self.acme.new_order.return_value = self.eg_order
        self.eg_order.update.return_value = self.eg_order

    def _check_obtain_certificate(self, auth_count=1):
        if auth_count == 1:
            self.client.auth_handler.handle_authorizations.assert_called_once_with(
                self.eg_order,
                self.config.allow_subset_of_names)
        else:
            self.assertEqual(self.client.auth_handler.handle_authorizations.call_count, auth_count)

        self.acme.finalize_order.assert_called_once_with(
            self.eg_order, mock.ANY,
            fetch_alternative_chains=self.config.preferred_chain is not None)

    @mock.patch("certbot._internal.client.crypto_util")
    @mock.patch("certbot._internal.client.logger")
    @test_util.patch_get_utility()
    def test_obtain_certificate_from_csr(self, unused_mock_get_utility,
                                         mock_logger, mock_crypto_util):
        self._mock_obtain_certificate()
        test_csr = util.CSR(form="pem", file=None, data=CSR_SAN)
        auth_handler = self.client.auth_handler
        self._set_mock_from_fullchain(mock_crypto_util.cert_and_chain_from_fullchain)

        orderr = self.acme.new_order(test_csr.data)
        auth_handler.handle_authorizations(orderr, False)
        self.assertEqual(
            (mock.sentinel.cert, mock.sentinel.chain),
            self.client.obtain_certificate_from_csr(
                test_csr,
                orderr=orderr))
        mock_crypto_util.find_chain_with_issuer.assert_not_called()
        # and that the cert was obtained correctly
        self._check_obtain_certificate()

        # Test that --preferred-chain results in chain selection
        self.config.preferred_chain = "some issuer"
        self.assertEqual(
            (mock.sentinel.cert, mock.sentinel.chain),
            self.client.obtain_certificate_from_csr(
                test_csr,
                orderr=orderr))
        mock_crypto_util.find_chain_with_issuer.assert_called_once_with(
            [orderr.fullchain_pem] + orderr.alternative_fullchains_pem,
            "some issuer", True)
        self.config.preferred_chain = None

        # Test for orderr=None
        self.assertEqual(
            (mock.sentinel.cert, mock.sentinel.chain),
            self.client.obtain_certificate_from_csr(
                test_csr,
                orderr=None))
        auth_handler.handle_authorizations.assert_called_with(self.eg_order, False)

        # Test for no auth_handler
        self.client.auth_handler = None
        self.assertRaises(
            errors.Error,
            self.client.obtain_certificate_from_csr,
            test_csr)
        mock_logger.warning.assert_called_once_with(mock.ANY)

    @mock.patch("certbot._internal.client.crypto_util")
    def test_obtain_certificate(self, mock_crypto_util):
        csr = util.CSR(form="pem", file=None, data=CSR_SAN)
        mock_crypto_util.init_save_csr.return_value = csr
        mock_crypto_util.init_save_key.return_value = mock.sentinel.key
        self._set_mock_from_fullchain(mock_crypto_util.cert_and_chain_from_fullchain)

        self._test_obtain_certificate_common(mock.sentinel.key, csr)

        mock_crypto_util.init_save_key.assert_called_once_with(
            key_size=self.config.rsa_key_size,
            key_dir=self.config.key_dir,
            key_type=self.config.key_type,
            elliptic_curve=None,  # elliptic curve is not set
        )
        mock_crypto_util.init_save_csr.assert_called_once_with(
            mock.sentinel.key, self.eg_domains, self.config.csr_dir)
        mock_crypto_util.cert_and_chain_from_fullchain.assert_called_once_with(
            self.eg_order.fullchain_pem)

    @mock.patch("certbot._internal.client.crypto_util")
    @mock.patch("certbot.compat.os.remove")
    def test_obtain_certificate_partial_success(self, mock_remove, mock_crypto_util):
        csr = util.CSR(form="pem", file=mock.sentinel.csr_file, data=CSR_SAN)
        key = util.CSR(form="pem", file=mock.sentinel.key_file, data=CSR_SAN)
        mock_crypto_util.init_save_csr.return_value = csr
        mock_crypto_util.init_save_key.return_value = key
        self._set_mock_from_fullchain(mock_crypto_util.cert_and_chain_from_fullchain)

        authzr = self._authzr_from_domains(["example.com"])
        self.config.allow_subset_of_names = True
        self._test_obtain_certificate_common(key, csr, authzr_ret=authzr, auth_count=2)

        self.assertEqual(mock_crypto_util.init_save_key.call_count, 2)
        self.assertEqual(mock_crypto_util.init_save_csr.call_count, 2)
        self.assertEqual(mock_remove.call_count, 2)
        self.assertEqual(mock_crypto_util.cert_and_chain_from_fullchain.call_count, 1)

    @mock.patch("certbot._internal.client.crypto_util")
    @mock.patch("certbot._internal.client.acme_crypto_util")
    def test_obtain_certificate_dry_run(self, mock_acme_crypto, mock_crypto):
        csr = util.CSR(form="pem", file=None, data=CSR_SAN)
        mock_acme_crypto.make_csr.return_value = CSR_SAN
        mock_crypto.make_key.return_value = mock.sentinel.key_pem
        key = util.Key(file=None, pem=mock.sentinel.key_pem)
        self._set_mock_from_fullchain(mock_crypto.cert_and_chain_from_fullchain)

        self.client.config.dry_run = True
        self._test_obtain_certificate_common(key, csr)

        mock_crypto.make_key.assert_called_once_with(
            bits=self.config.rsa_key_size,
            elliptic_curve=None,  # not making an elliptic private key
            key_type=self.config.key_type,
        )
        mock_acme_crypto.make_csr.assert_called_once_with(
            mock.sentinel.key_pem, self.eg_domains, self.config.must_staple)
        mock_crypto.init_save_key.assert_not_called()
        mock_crypto.init_save_csr.assert_not_called()
        self.assertEqual(mock_crypto.cert_and_chain_from_fullchain.call_count, 1)

    @mock.patch("certbot._internal.client.logger")
    @mock.patch("certbot._internal.client.crypto_util")
    @mock.patch("certbot._internal.client.acme_crypto_util")
    def test_obtain_certificate_dry_run_authz_deactivations_failed(self, mock_acme_crypto,
                                                                   mock_crypto, mock_log):
        from acme import messages
        csr = util.CSR(form="pem", file=None, data=CSR_SAN)
        mock_acme_crypto.make_csr.return_value = CSR_SAN
        mock_crypto.make_key.return_value = mock.sentinel.key_pem
        key = util.Key(file=None, pem=mock.sentinel.key_pem)
        self._set_mock_from_fullchain(mock_crypto.cert_and_chain_from_fullchain)

        self._mock_obtain_certificate()
        self.client.config.dry_run = True

        # Two authzs that are already valid and should get deactivated (dry run)
        authzrs = self._authzr_from_domains(["example.com", "www.example.com"])
        for authzr in authzrs:
            authzr.body.status = messages.STATUS_VALID

        # One deactivation succeeds, one fails
        auth_handler = self.client.auth_handler
        auth_handler.deactivate_valid_authorizations.return_value = ([authzrs[0]], [authzrs[1]])

        # Certificate should get issued despite one failed deactivation
        self.eg_order.authorizations = authzrs
        self.client.auth_handler.handle_authorizations.return_value = authzrs
        with test_util.patch_get_utility():
            result = self.client.obtain_certificate(self.eg_domains)
        self.assertEqual(result, (mock.sentinel.cert, mock.sentinel.chain, key, csr))
        self._check_obtain_certificate(1)

        # Deactivation success/failure should have been handled properly
        self.assertEqual(auth_handler.deactivate_valid_authorizations.call_count, 1,
                        "Deactivate authorizations should be called")
        self.assertEqual(self.acme.new_order.call_count, 2,
                        "Order should be recreated due to successfully deactivated authorizations")
        mock_log.warning.assert_called_with("Certbot was unable to obtain fresh authorizations for"
                                            " every domain. The dry run will continue, but results"
                                            " may not be accurate.")

    def _set_mock_from_fullchain(self, mock_from_fullchain):
        mock_cert = mock.Mock()
        mock_cert.encode.return_value = mock.sentinel.cert
        mock_chain = mock.Mock()
        mock_chain.encode.return_value = mock.sentinel.chain
        mock_from_fullchain.return_value = (mock_cert, mock_chain)

    def _authzr_from_domains(self, domains):
        authzr = []

        # domain ordering should not be affected by authorization order
        for domain in reversed(domains):
            authzr.append(
                mock.MagicMock(
                    body=mock.MagicMock(
                        identifier=mock.MagicMock(
                            value=domain))))
        return authzr

    def _test_obtain_certificate_common(self, key, csr, authzr_ret=None, auth_count=1):
        self._mock_obtain_certificate()

        # return_value is essentially set to (None, None) in
        # _mock_obtain_certificate(), which breaks this test.
        # Thus fixed by the next line.
        authzr = authzr_ret or self._authzr_from_domains(self.eg_domains)

        self.eg_order.authorizations = authzr
        self.client.auth_handler.handle_authorizations.return_value = authzr

        with test_util.patch_get_utility():
            result = self.client.obtain_certificate(self.eg_domains)

        self.assertEqual(
            result,
            (mock.sentinel.cert, mock.sentinel.chain, key, csr))
        self._check_obtain_certificate(auth_count)

    @mock.patch('certbot._internal.client.Client.obtain_certificate')
    @mock.patch('certbot._internal.storage.RenewableCert.new_lineage')
    def test_obtain_and_enroll_certificate(self,
                                           mock_storage, mock_obtain_certificate):
        domains = ["*.example.com", "example.com"]
        mock_obtain_certificate.return_value = (mock.MagicMock(),
                                                mock.MagicMock(), mock.MagicMock(), None)

        self.client.config.dry_run = False
        self.assertTrue(self.client.obtain_and_enroll_certificate(domains, "example_cert"))

        self.assertTrue(self.client.obtain_and_enroll_certificate(domains, None))
        self.assertTrue(self.client.obtain_and_enroll_certificate(domains[1:], None))

        self.client.config.dry_run = True

        self.assertFalse(self.client.obtain_and_enroll_certificate(domains, None))

        names = [call[0][0] for call in mock_storage.call_args_list]
        self.assertEqual(names, ["example_cert", "example.com", "example.com"])

    @mock.patch("certbot._internal.cli.helpful_parser")
    def test_save_certificate(self, mock_parser):
        certs = ["cert_512.pem", "cert-san_512.pem"]
        tmp_path = tempfile.mkdtemp()

        cert_pem = test_util.load_vector(certs[0])
        chain_pem = (test_util.load_vector(certs[0]) + test_util.load_vector(certs[1]))
        candidate_cert_path = os.path.join(tmp_path, "certs", "cert_512.pem")
        candidate_chain_path = os.path.join(tmp_path, "chains", "chain.pem")
        candidate_fullchain_path = os.path.join(tmp_path, "chains", "fullchain.pem")
        mock_parser.verb = "certonly"
        mock_parser.args = ["--cert-path", candidate_cert_path,
                            "--chain-path", candidate_chain_path,
                            "--fullchain-path", candidate_fullchain_path]

        cert_path, chain_path, fullchain_path = self.client.save_certificate(
            cert_pem, chain_pem, candidate_cert_path, candidate_chain_path,
            candidate_fullchain_path)

        self.assertEqual(os.path.dirname(cert_path),
                         os.path.dirname(candidate_cert_path))
        self.assertEqual(os.path.dirname(chain_path),
                         os.path.dirname(candidate_chain_path))
        self.assertEqual(os.path.dirname(fullchain_path),
                         os.path.dirname(candidate_fullchain_path))

        with open(cert_path, "rb") as cert_file:
            cert_contents = cert_file.read()
        self.assertEqual(cert_contents, test_util.load_vector(certs[0]))

        with open(chain_path, "rb") as chain_file:
            chain_contents = chain_file.read()
        self.assertEqual(chain_contents, test_util.load_vector(certs[0]) +
                         test_util.load_vector(certs[1]))

        shutil.rmtree(tmp_path)

    def test_deploy_certificate_success(self):
        self.assertRaises(errors.Error, self.client.deploy_certificate,
                          ["foo.bar"], "key", "cert", "chain", "fullchain")

        installer = mock.MagicMock()
        self.client.installer = installer

        self.client.deploy_certificate(
            ["foo.bar"], "key", "cert", "chain", "fullchain")
        installer.deploy_cert.assert_called_once_with(
            cert_path=os.path.abspath("cert"),
            chain_path=os.path.abspath("chain"),
            domain='foo.bar',
            fullchain_path='fullchain',
            key_path=os.path.abspath("key"))
        self.assertEqual(installer.save.call_count, 2)
        installer.restart.assert_called_once_with()

    def test_deploy_certificate_failure(self):
        installer = mock.MagicMock()
        self.client.installer = installer

        installer.deploy_cert.side_effect = errors.PluginError
        self.assertRaises(errors.PluginError, self.client.deploy_certificate,
                          ["foo.bar"], "key", "cert", "chain", "fullchain")
        installer.recovery_routine.assert_called_once_with()

    def test_deploy_certificate_save_failure(self):
        installer = mock.MagicMock()
        self.client.installer = installer

        installer.save.side_effect = errors.PluginError
        self.assertRaises(errors.PluginError, self.client.deploy_certificate,
                          ["foo.bar"], "key", "cert", "chain", "fullchain")
        installer.recovery_routine.assert_called_once_with()

    @test_util.patch_get_utility()
    def test_deploy_certificate_restart_failure(self, mock_get_utility):
        installer = mock.MagicMock()
        installer.restart.side_effect = [errors.PluginError, None]
        self.client.installer = installer

        self.assertRaises(errors.PluginError, self.client.deploy_certificate,
                          ["foo.bar"], "key", "cert", "chain", "fullchain")
        self.assertEqual(mock_get_utility().add_message.call_count, 1)
        installer.rollback_checkpoints.assert_called_once_with()
        self.assertEqual(installer.restart.call_count, 2)

    @test_util.patch_get_utility()
    def test_deploy_certificate_restart_failure2(self, mock_get_utility):
        installer = mock.MagicMock()
        installer.restart.side_effect = errors.PluginError
        installer.rollback_checkpoints.side_effect = errors.ReverterError
        self.client.installer = installer

        self.assertRaises(errors.PluginError, self.client.deploy_certificate,
                          ["foo.bar"], "key", "cert", "chain", "fullchain")
        self.assertEqual(mock_get_utility().add_message.call_count, 1)
        installer.rollback_checkpoints.assert_called_once_with()
        self.assertEqual(installer.restart.call_count, 1)


class EnhanceConfigTest(ClientTestCommon):
    """Tests for certbot._internal.client.Client.enhance_config."""

    def setUp(self):
        super().setUp()

        self.config.hsts = False
        self.config.redirect = False
        self.config.staple = False
        self.config.uir = False
        self.domain = "example.org"

    def test_no_installer(self):
        self.assertRaises(
            errors.Error, self.client.enhance_config, [self.domain], None)

    def test_unsupported(self):
        self.client.installer = mock.MagicMock()
        self.client.installer.supported_enhancements.return_value = []

        self.config.redirect = None
        self.config.hsts = True
        with mock.patch("certbot._internal.client.logger") as mock_logger:
            self.client.enhance_config([self.domain], None)
        self.assertEqual(mock_logger.warning.call_count, 1)
        self.client.installer.enhance.assert_not_called()

    @mock.patch("certbot._internal.client.logger")
    def test_already_exists_header(self, mock_log):
        self.config.hsts = True
        self._test_with_already_existing()
        self.assertIs(mock_log.warning.called, True)
        self.assertEqual(mock_log.warning.call_args[0][1],
                          'Strict-Transport-Security')

    @mock.patch("certbot._internal.client.logger")
    def test_already_exists_redirect(self, mock_log):
        self.config.redirect = True
        self._test_with_already_existing()
        self.assertIs(mock_log.warning.called, True)
        self.assertEqual(mock_log.warning.call_args[0][1],
                          'redirect')

    @mock.patch("certbot._internal.client.logger")
    def test_config_set_no_warning_redirect(self, mock_log):
        self.config.redirect = False
        self._test_with_already_existing()
        self.assertIs(mock_log.warning.called, False)

    @mock.patch("certbot._internal.client.logger")
    def test_no_warn_redirect(self, mock_log):
        self.config.redirect = None
        self._test_with_all_supported()
        self.assertIs(mock_log.warning.called, False)

    def test_no_ask_hsts(self):
        self.config.hsts = True
        self._test_with_all_supported()
        self.client.installer.enhance.assert_called_with(
            self.domain, "ensure-http-header", "Strict-Transport-Security")

    def test_no_ask_redirect(self):
        self.config.redirect = True
        self._test_with_all_supported()
        self.client.installer.enhance.assert_called_with(
            self.domain, "redirect", None)

    def test_no_ask_staple(self):
        self.config.staple = True
        self._test_with_all_supported()
        self.client.installer.enhance.assert_called_with(
            self.domain, "staple-ocsp", None)

    def test_no_ask_uir(self):
        self.config.uir = True
        self._test_with_all_supported()
        self.client.installer.enhance.assert_called_with(
            self.domain, "ensure-http-header", "Upgrade-Insecure-Requests")

    def test_enhance_failure(self):
        self.client.installer = mock.MagicMock()
        self.client.installer.enhance.side_effect = errors.PluginError
        self._test_error()
        self.client.installer.recovery_routine.assert_called_once_with()

    def test_save_failure(self):
        self.client.installer = mock.MagicMock()
        self.client.installer.save.side_effect = errors.PluginError
        self._test_error()
        self.client.installer.recovery_routine.assert_called_once_with()
        self.client.installer.save.assert_called_once_with(mock.ANY)

    def test_restart_failure(self):
        self.client.installer = mock.MagicMock()
        self.client.installer.restart.side_effect = [errors.PluginError, None]
        self._test_error_with_rollback()

    def test_restart_failure2(self):
        installer = mock.MagicMock()
        installer.restart.side_effect = errors.PluginError
        installer.rollback_checkpoints.side_effect = errors.ReverterError
        self.client.installer = installer
        self._test_error_with_rollback()

    def _test_error_with_rollback(self):
        self._test_error()
        self.assertIs(self.client.installer.restart.called, True)

    def _test_error(self):
        self.config.redirect = True
        with test_util.patch_get_utility() as mock_gu:
            self.assertRaises(
                errors.PluginError, self._test_with_all_supported)
        self.assertEqual(mock_gu().add_message.call_count, 1)

    def _test_with_all_supported(self):
        if self.client.installer is None:
            self.client.installer = mock.MagicMock()
        self.client.installer.supported_enhancements.return_value = [
            "ensure-http-header", "redirect", "staple-ocsp"]
        self.client.enhance_config([self.domain], None)
        self.assertEqual(self.client.installer.save.call_count, 1)
        self.assertEqual(self.client.installer.restart.call_count, 1)

    def _test_with_already_existing(self):
        self.client.installer = mock.MagicMock()
        self.client.installer.supported_enhancements.return_value = [
            "ensure-http-header", "redirect", "staple-ocsp"]
        self.client.installer.enhance.side_effect = errors.PluginEnhancementAlreadyPresent()
        self.client.enhance_config([self.domain], None)


class RollbackTest(unittest.TestCase):
    """Tests for certbot._internal.client.rollback."""

    def setUp(self):
        self.m_install = mock.MagicMock()

    @classmethod
    def _call(cls, checkpoints, side_effect):
        from certbot._internal.client import rollback
        with mock.patch("certbot._internal.client.plugin_selection.pick_installer") as mpi:
            mpi.side_effect = side_effect
            rollback(None, checkpoints, {}, mock.MagicMock())

    def test_no_problems(self):
        self._call(1, self.m_install)
        self.assertEqual(self.m_install().rollback_checkpoints.call_count, 1)
        self.assertEqual(self.m_install().restart.call_count, 1)

    def test_no_installer(self):
        self._call(1, None)  # Just make sure no exceptions are raised


if __name__ == "__main__":
    unittest.main()  # pragma: no cover
