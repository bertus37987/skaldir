# A REAL HTTP service for the HTTP-surface-adapter tests (EXPAND, gap A).
#
# It is deliberately not a mock: busybox httpd serves a static OpenAPI document
# and runs shell CGIs that compute a sha-256 / echo a value with busybox's own
# `sha256sum`. The local oracle of the test is Python's hashlib — two independent
# implementations of the same operation must agree, which is the whole point of
# the independent-oracle rule.
#
# Base image `busybox:1.36` is the official multi-arch busybox image (Debian
# based, MIT/GPL-2.0 busybox). Nothing is pulled by the tests that use this
# fixture: the build is FROM a local image only.
#
# Ownership labels: the image (and every container started from it by a test)
# carries godsv.* labels, so a leftover can be found and removed by label —
# never by name, never by a blanket prune.
FROM busybox:1.36
LABEL godsv.owner="skaldir" godsv.purpose="test" godsv.capability="http-fixture"
COPY www /www
EXPOSE 8080
ENTRYPOINT []
CMD ["httpd", "-f", "-p", "8080", "-h", "/www"]
