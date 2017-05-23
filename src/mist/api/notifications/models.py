import re

import mongoengine as me

from mist.api.users.models import Owner

# NotificationPolicy -> NotificationRule(List)   -> NotificationOperator(List)


class NotificationOperator(me.EmbeddedDocument):
    """
    Represents a single Notifications operator, which
    essentially corresponds to an allow/block action
    """
    channel = me.StringField(max_length=64, required=True, default="")
    value = me.StringField(max_length=7, required=True,
                           choices=('ALLOW', 'INHERIT', 'BLOCK'))


class NotificationRule(me.EmbeddedDocument):
    """
    Represents a single Notifications rule, which includes
    a filter expression and a list of operators of length equal
    to the number of channels defined in the parent policy
    """
    type = me.StringField(max_length=200, required=True, default="")
    action = me.StringField(max_length=200, required=True, default="")
    tags = me.StringField(max_length=200, required=True, default="")
    operators = me.EmbeddedDocumentListField(NotificationOperator)

    def set_channel(self, channel, value='ALLOW'):
        existing_operator = self.operators(channel=channel)
        if not existing_operator:
            new_operator = NotificationOperator()
            new_operator.channel = channel
            new_operator.value = value
            new_operator.save() #todo: is this needed?
            self.operators.add(new_operator)
        else:
            existing_operator.value = value
            existing_operator.save()

    def channels_for_notification(self, notification, inherited_channels=[]):
        """
        Accepts a notification and checks its validity against
        the rule, returning the list of channels which are allowed.
        
        The method also accepts an optional list of inherited channels (e.g.
        from another, higher-level policy). In this case, it performs a difference
        with those inherited channels with the blocked channels of the current
        policy, and performs a union with the result with the allowed channels
        of the current policy.
        """
        if self.notification_matches_rule(notification):
            allowed_ch = set([op.channel for op in self.operators if op.value == 'ALLOW'])
            blocked_ch = set([op.channel for op in self.operators if op.value == 'BLOCK'])
            inherited_ch = set(inherited_channels)
            return list(allowed_ch + (inherited_ch - blocked_ch))

    def notification_matches_rule(self, notification):
        """
        Accepts a notification and returns whether
        the notification type, action and tags match the corresponding
        rule entries
        """
        return (notification.type == self.type 
                and notification.action == self.action 
                and notification.tags == self.tags)


class NotificationPolicy(me.EmbeddedDocument):
    """
    Represents a policy for pushing notifications
    through different channels, according to one or more
    rules.
    """
    owner = me.EmbeddedDocumentField(Owner)
    rules = me.EmbeddedDocumentListField(NotificationRule)
    channels = me.StringListField()

    def add_channel(self, channel):
        """
        Accepts a channel name and adds it to the channel list
        and to each of the rules' channel list
        """
        if channel not in self.channels:
            channels.add(channel)
        for rule in self.rules:
            rule.add_channel(channel)

    def channels_for_notification(self, notification, inherited_channels=None):
        """
        Accepts a notification instance and returns a list of channel
        instances through which the notification should be pushed, 
        by checking against the policy's rules.
        """
        matching_channels = Set()
        for rule in self.rules:
            new_channels = Set(rule.channels_for_notification(notification, inherited_channels))
            matching_channels = channels.union(new_channels)
        return matching_channels

    def get_channels(self, rtype = None):
        """
        Returns policy channels that any of the policy's, rules
        refer to, optionally filtering by one or more rule types.
        """
        pass

    def get_rules(self, ctype = None):
        """
        Returns policy rules, optionally filtering by one
        or more channel ids.
        """
        pass


class Notification():
    # todo: allow custom fields
    def __init__(self, message, subject, type, action=None):
        self.message = message
        self.subject = subject
        self.type = type
        self.action = action