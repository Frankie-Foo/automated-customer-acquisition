from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from sales_automation.services.mailbox import MailboxReplyService


@pytest.mark.parametrize('direct,expected', [('<new@test>', '<new@test>'), ('<unknown@test>', None), (None, None)])
def test_direct_reply_precedes_ancestors_and_ancestors_do_not_prove_attribution(direct, expected):
    class Repo:
        def find_contact_id_by_message_id(self, message_id):
            return 42 if message_id in {'<new@test>', '<old@test>'} else None

        def find_contact_id_by_email(self, email):
            return 42

    message = EmailMessage()
    if direct:
        message['In-Reply-To'] = direct
    message['References'] = '<old@test>'
    service = MailboxReplyService(SimpleNamespace(raw={}), Repo())
    assert service._match_contact(message, 'customer@test') == (42, expected)
