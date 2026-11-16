import json
import pytest
from aurora_agent.stage_g import (StageGClient, AuthorizationDenied, MissingEvidence,
    UnsupportedProfile, SealPending, ProtocolError, UncertainExecution)
from aurora_agent.stage_g.client import json_response
from aurora_agent.stage_g.contract import canonical, parse, Rejected
from aurora_agent.stage_g.transport import HTTPS, Response

class Fake:
    def __init__(self, response): self.response, self.calls = response, []
    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.response, Exception): raise self.response
        return self.response

@pytest.mark.parametrize('url', ['http://localhost', 'https://user:secret@host', 'https://host/#x',
    'https://host/?token=x', 'https://host/a/../b', 'https://host/%2e%2e', 'https://host/ bad'])
def test_only_fixed_https_endpoints(url):
    with pytest.raises(ProtocolError): HTTPS(url)

@pytest.mark.parametrize('status,code,cls', [(403,'PEER_NOT_AUTHORIZED',AuthorizationDenied),
    (409,'TSA_TOKEN_MISSING',MissingEvidence), (409,'PROFILE_UNSUPPORTED',UnsupportedProfile),
    (409,'SEAL_RECEIPT_PENDING',SealPending)])
def test_typed_errors(status, code, cls):
    with pytest.raises(cls): json_response(Response(status, {'content-type':'application/json'}, canonical({'error':code})))

@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":null}', b'{"x":1.1}', b'{"x":-1}'])
def test_g_parser_rejects_ambiguous_values(raw):
    with pytest.raises(Rejected): parse(raw)

def test_transport_failure_is_not_retried_or_given_authority():
    fake=Fake(UncertainExecution())
    client=StageGClient('https://fixed.invalid/v1/stage-g','TEST_ONLY','tenant.test',transport=fake)
    with pytest.raises(UncertainExecution) as e: client.projection('action')
    assert e.value.recovery=='QUERY_ONLY' and len(fake.calls)==1

def test_non_json_response_and_no_automatic_publication():
    with pytest.raises(ProtocolError): json_response(Response(200, {'content-type':'text/html'}, b'{}'))
    fake=Fake(None);client=StageGClient('https://fixed.invalid','TEST_ONLY','tenant.test',transport=fake)
    with pytest.raises(ProtocolError): client.publish('action',publication_enabled='true')
    assert fake.calls==[]

def test_review_link_carries_only_tenant_and_action_identifiers():
    client=StageGClient('https://fixed.invalid','TEST_ONLY_SECRET','tenant.test',transport=Fake(None))
    url=client.review_url('https://auroraseal.com','action.test')
    assert url=='https://auroraseal.com/app/actions?organization_id=tenant.test&action_id=action.test'
    assert 'SECRET' not in url
    with pytest.raises(ProtocolError):client.review_url('https://user:pass@host','action')
