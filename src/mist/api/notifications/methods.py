
from mist.api.users.models import User, Organization
import models
import channels


def send_notification(notification):
    '''
    Accepts a notification instance, checks against user
    notification policy and sends the notification
    through specified channels.
    '''
    policy = get_policy(notification["user"], notification["org"])
    if policy.notification_allowed(notification):
        chan = channels.channel_instance_with_name(notification["channel"])
        if chan:
            chan.send(notification)


def add_block_rule(user, org, source):
    '''
    Adds a block rule to a user-org policy for the specified source.
    Creates the policy if it does not exist.
    '''
    policy = get_policy(user, org)
    rules = [rule for rule in policy.rules if rule.source == source]
    if not rules:
        rule = models.NotificationRule()
        rule.source = source
        rule.value = "BLOCK"
        policy.rules.append(rule)
        policy.save()


def remove_block_rule(user, org, source):
    '''
    Removes a block rule to a user-org policy for the specified source.
    Creates the policy if it does not exist.
    '''
    policy = get_policy(user, org)
    rules = [rule for rule in policy.rules if rule.source == source]
    if rules:
        policy.rules.remove(rules[0])
        policy.save()


def has_block_rule(user, org, source):
    '''
    Accepts a user and org and queries whether
    there is a block rule in place.
    Creates the policy if it does not exist.
    '''
    policy = get_policy(user, org)
    rules = [rule for rule in policy.rules if rule.source == source]
    if rules:
        return True
    return False


def get_policy(user, org, create=True):
    '''
    Accepts a user-org pair and returns the corresponding notification
    policy, with the option to create one if not exist.
    '''
    policies = models.UserNotificationPolicy.objects(
        user=user, organization=org)
    if not policies:
        if create:
            policy = models.UserNotificationPolicy()
            policy.user = user
            policy.organization = org
            policy.save()
            return policy
        else:
            return None
    return policies[0]


def make_notification(
        subject,
        body,
        source,
        channel,
        user,
        org,
        summary=None,
        html_body=None):
    '''
    Generates a notification dictionary
    '''
    # notification fields (in par. are optional):
    # subject
    # (summary)
    # body
    # (html_body)
    # source
    # channel
    # user
    # org
    notification = {
        "subject": subject,
        "body": body,
        "source": source,
        "channel": channel,
        "user": user,
        "org": org
    }
    if summary:
        notification["summary"] = summary
    if html_body:
        notification["html_body"] = html_body
    return notification


def test():
    '''
    Test this
    '''
    user = User.objects.get(email="yconst@mist.io")
    org = Organization.objects.get(members=user)

    notification = make_notification("some spam", "more spam",
                                     "alerts", "stdout", user, org)

    # first send with no rules - it should pass
    remove_block_rule(user, org, "alerts")
    print "Sending with no rules - message should appear below:"
    send_notification(notification)

    # now create a rule - it should fail
    add_block_rule(user, org, "alerts")
    print "Sending with block rule - nothing should appear below:"
    send_notification(notification)


if __name__ == "__main__":
    test()
