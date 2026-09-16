# 3GPP intermediate certificate

`sectigo-public-server-authentication-ca-ov-r36.pem` is the public **Sectigo
Public Server Authentication CA OV R36** intermediate. It is not a private key
or a new root CA.

- Source: http://crt.sectigo.com/SectigoPublicServerAuthenticationCAOVR36.crt
  (the CA Issuers URL in the live `*.3gpp.org` certificate).
- Official catalog: https://www.sectigo.com/faqs/detail/Sectigo-Public-Intermediates-and-Roots
- DER SHA-256: `6542d176bed50f193c0ce297ae44ecd8a0a86bec2ede682769344059b4e78530`
- Validity: 2021-03-22 through 2036-03-21.

On 2026-09-16, the 3GPP server sent only its leaf certificate, causing Python
`CERTIFICATE_VERIFY_FAILED` and upstream HTTP 526 errors. This intermediate
validates against the existing Sectigo Root R46 in certifi. The bundle script
checks that signature chain with OpenSSL before supplementing certifi. TLS and
hostname verification stay enabled. No certificate is fetched dynamically in CI.

When the issuer changes, obtain and verify the new intermediate against existing
trusted roots, update this record, and rerun the network diagnostic. When the
server consistently supplies a complete chain, this supplement can be removed.
