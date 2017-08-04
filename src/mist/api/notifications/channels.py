import json
import jsonpatch

from mist.api import config
from mist.api.helpers import send_email, amqp_publish_user

from models import Notification

import logging


class BaseChannel():
    '''
    Base notification channel class
    '''

    def send(self, notification):
        '''
        Accepts a notification and sends it using the
        current channel instance.
        '''
        pass

    def delete(self, notification):
        '''
        Accepts a notification and deletes it
        if it had been saved
        '''
        pass

    def dismiss(self, notification):
        '''
        Accepts a notification and marks it as
        dismissed by the user
        '''
        pass


class EmailReportsChannel(BaseChannel):
    '''
    Email channel for reports.
    Tries to send using Sendgrid, if credentials are available
    in config, otherwise sends email using SMTP.
    '''

    def send(self, notification):
        '''
        Accepts a notification and sends an email using included data.
        If SENDGRID_REPORTING_KEY and EMAIL_REPORT_SENDER are available
        in config, it uses Sendgrid to deliver the email. Otherwise, it
        uses plain SMTP through send_email()
        '''
        user = notification.user

        to = notification.email or user.email
        full_name = user.get_nice_name()
        first_name = user.first_name or user.get_nice_name()

        if (hasattr(config, "SENDGRID_REPORTING_KEY") and
                hasattr(config, "EMAIL_REPORT_SENDER")):
            from sendgrid.helpers.mail import (Email,
                                               Mail,
                                               Personalization,
                                               Content,
                                               Substitution)
            import sendgrid

            self.sg_instance = sendgrid.SendGridAPIClient(
                apikey=config.SENDGRID_REPORTING_KEY)

            mail = Mail()
            mail.from_email = Email(
                config.EMAIL_REPORT_SENDER,
                "Mist.io Reports")
            personalization = Personalization()
            personalization.add_to(Email(to, full_name))
            personalization.subject = notification.subject
            sub1 = Substitution("%name%", first_name)
            personalization.add_substitution(sub1)
            if "unsub_link" in notification:
                sub2 = Substitution("%nsub%", notification.unsub_link)
                personalization.add_substitution(sub2)
            mail.add_personalization(personalization)

            mail.add_content(Content("text/plain", notification.body))
            if "html_body" in notification:
                mail.add_content(
                    Content(
                        "text/html",
                        notification.html_body))

            mdict = mail.get()
            try:
                return self.sg_instance.client.mail.send.post(
                    request_body=mdict)
            except Exception as exc:
                logging.error(str(exc.status_code) + ' - ' + exc.reason)
                logging.error(exc.to_dict)
                exit()
        else:
            send_email(notification.subject, notification.body,
                       [to], sender="config.EMAIL_REPORT_SENDER")


class InAppChannel(BaseChannel):
    '''
    In-app Notifications channel
    Manages notifications and triggers session updates
    '''

    def send(self, notification):

        def modify(notification):
            # dismiss similar notifications if unique
            # deleting could also be an option
            if notification.unique:
                similar = Notification.objects(user=notification.user,
                    organization=notification.organization,
                    channel=notification.channel,
                    source=notification.source,
                    resource=notification.resource,
                    kind=notification.kind,
                    dismissed=False
                    )
                for item in similar:
                    item.dismissed = True
                    item.save()
            notification.save()

        self._modify_and_notify(notification, modify)

    def delete(self, notification):

        def modify(notification):
            notification.delete()

        self._modify_and_notify(notification, modify)

    def dismiss(self, notification):

        def modify(notification):
            notification.dismissed = True
            notification.save()

        self._modify_and_notify(notification, modify)

    def _modify_and_notify(self, notification, modifier):
        user = notification.user

        old_notifications = [obj for obj in Notification.objects(
            user=user, channel='in_app', dismissed=False)]
        modifier(notification)
        new_notifications = [obj for obj in Notification.objects(
            user=user, channel='in_app', dismissed=False)]
        patch = jsonpatch.JsonPatch.from_diff(
            old_notifications, new_notifications).patch

        if patch:
            data = json.dumps({
                "user": user.id,
                "patch": patch
            }, cls=NotificationsEncoder)
            amqp_publish_user(notification.organization,
                              routing_key='patch_notifications',
                              data=data)


class StdoutChannel(BaseChannel):
    '''
    Stdout channel, mainly for testing/debugging
    '''

    def send(self, notification):
        print notification.subject
        if "summary" in notification:
            print notification.summary
        print notification.body


def channel_instance_with_name(name):
    '''
    Accepts a string and returns a channel instance with
    matching name or None
    '''
    if name == 'stdout':
        return StdoutChannel()
    elif name == 'email_reports':
        return EmailReportsChannel()
    elif name == 'in_app':
        return InAppChannel()
    return None


class NotificationsEncoder(json.JSONEncoder):
    '''
    JSON Encoder that properly handles Notification
    instances
    '''
    def default(self, o):
        # FIXME: this is kind of dumb, but it works
        if isinstance(o, Notification):
            return json.loads(o.to_json())
        else:
            return json.JSONEncoder.default(self, o)
