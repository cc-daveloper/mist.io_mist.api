"""Definition of `BaseDNSController`

The `BaseDNSController` is a sub-controller, which is set as an attribute to a
`BaseController` class. The `BaseDNSController` is responsible for interacting
with libcloud's DNS API.

"""
import ssl
import logging
import datetime

import mongoengine as me

from mist.api.clouds.controllers.base import BaseController

from libcloud.common.types import InvalidCredsError
from libcloud.dns.types import ZoneDoesNotExistError, RecordDoesNotExistError

from mist.api.exceptions import CloudUnavailableError
from mist.api.exceptions import CloudUnauthorizedError
from mist.api.exceptions import ZoneNotFoundError
from mist.api.exceptions import RecordNotFoundError
from mist.api.exceptions import BadRequestError
from mist.api.exceptions import RecordExistsError
from mist.api.exceptions import ZoneExistsError
from mist.api.exceptions import ZoneCreationError
from mist.api.exceptions import RecordCreationError


log = logging.getLogger(__name__)


class BaseDNSController(BaseController):
    """Base class to be inherited by every clouds that supports a DNS
    sub-controller.

    This base controller factors out all the basic steps common to all or
    most clouds into a base class, and defines an interface for provider or
    technology specific cloud controllers.

    Subclasses are meant to extend or override methods of this base class to
    account for differencies between different cloud types.

    Care should be taken when considering to add new methods to a subclass.
    All controllers should have the same interface, to the degree this is
    feasible. That is to say, don't add a new method to a subclass unless
    there is a very good reason to do so.

    The following convention is followed:

    Any methods and attributes that don't start with an underscore are the
    controller's public API.

    In the `BaseDNSController`, these public methods will in most cases contain
    a basic implementation that works for most clouds, along with the proper
    logging and error handling. In almost all cases, subclasses SHOULD NOT
    override or extend the public methods of `BaseController`. To account for
    cloud/subclass specific behaviour, one is expected to override the
    internal/private methods of `BaseDNSController`.

    Any methods and attributes that start with an underscore are the
    controller's internal/private API.

    To account for cloud/subclass specific behaviour, the public methods of
    `BaseDNSController` call a number of private methods. These methods will
    always start with an underscore, such as `_list_zones`.

    This `BaseDNSController` defines a strict interface to controlling clouds
    that allow for DNS specific actions.
    For each different cloud type, a subclass needs to be defined. Each
    subclass MUST receive its main controller as its sole init argument

    """

    def list_zones(self):
        """
        This is the public method to call when requesting all the DNS zones
        under a specific cloud.
        """

        # TODO: Adding here for circular dependency issue. Need to fix this.
        from mist.api.dns.models import Zone

        # Fetch zones from libcloud connection.
        pr_zones = self._list_zones__fetch_zones()

        zones = []
        new_zones = []
        for pr_zone in pr_zones:
            # FIXME: We are using the zone_id and owner instead of the
            # cloud_id to search for existing zones because providers
            # allow access to the same zone from multiple clouds so
            # we can end up adding the same zone many times under
            # different clouds.
            try:
                zones_q = Zone.objects(owner=self.cloud.owner,
                                       zone_id=pr_zone.id, deleted=None)
                for zone in zones_q:
                    if zone.cloud.ctl.provider == self.cloud.ctl.provider:
                        break
                else:
                    raise Zone.DoesNotExist
            except Zone.DoesNotExist:
                log.info("Zone: %s/domain: %s not in the database, creating.",
                         pr_zone.id, pr_zone.domain)
                zone = Zone(cloud=self.cloud, owner=self.cloud.owner,
                            zone_id=pr_zone.id)
                new_zones.append(zone)
            zone.domain = pr_zone.domain
            zone.type = pr_zone.type
            zone.ttl = pr_zone.ttl
            zone.extra = pr_zone.extra
            try:
                zone.save()
            except me.ValidationError as exc:
                log.error("Error updating %s: %s", zone, exc.to_dict())
                raise BadRequestError({'msg': exc.message,
                                       'errors': exc.to_dict()})
            except me.NotUniqueError as exc:
                log.error("Zone %s not unique error: %s", zone, exc)
                raise ZoneExistsError()
            zones.append(zone)
        self.cloud.owner.mapper.update(new_zones)

        # Delete any zones in the DB that were not returned by the provider
        # meaning they were deleted otherwise.
        Zone.objects(cloud=self.cloud, id__nin=[z.id for z in zones],
                     deleted=None).update(
                         set__deleted=datetime.datetime.utcnow())

        # Format zone information.
        return zones

    def _list_zones__fetch_zones(self):
        """
        Returns a list of available DNS zones for the cloud.
        This should not be overriden as the implementation is the same across
        all implemented DNS providers.

        """
        # Try to get the list of DNS zones from provider API.
        try:
            zones = self.connection.list_zones()
            log.info("List zones returned %d results for %s.",
                     len(zones), self.cloud)
            return zones
        except InvalidCredsError as exc:
            log.warning("Invalid creds on running list_zones on %s: %s",
                        self.cloud, exc)
            raise CloudUnauthorizedError()
        except ssl.SSLError as exc:
            log.error("SSLError on running list_zones on %s: %s",
                      self.cloud, exc)
            raise CloudUnavailableError(exc=exc)
        except Exception as exc:
            log.exception("Error while running list_zones on %s", self.cloud)
            raise CloudUnavailableError(exc=exc)

    def list_records(self, zone):
        """
        Public method to return a list of  records under a specific zone.
        """
        # Fetch records from libcloud connection.
        pr_records = self._list_records__fetch_records(zone.zone_id)

        # TODO: Adding here for circular dependency issue. Need to fix this.
        from mist.api.dns.models import Record, RECORDS

        records = []
        new_records = []
        for pr_record in pr_records:
            dns_cls = RECORDS[pr_record.type]
            try:
                record = Record.objects.get(zone=zone, record_id=pr_record.id,
                                            deleted=None)
            except Record.DoesNotExist:
                log.info("Record: %s not in the database, creating.",
                         pr_record.id)
                if pr_record.type not in RECORDS:
                    log.error("Unsupported record type '%s'", pr_record.type)
                    continue

                record = dns_cls(record_id=pr_record.id, zone=zone)
                new_records.append(record)
            # We need to check if any of the information returned by the
            # provider is different than what we have in the DB
            record.name = pr_record.name or ""
            record.type = pr_record.type
            record.ttl = pr_record.ttl
            record.extra = pr_record.extra

            self._list_records__postparse_data(pr_record, record)
            try:
                record.save()
            except me.ValidationError as exc:
                log.error("Error updating %s: %s", record, exc.to_dict())
                raise BadRequestError({'msg': exc.message,
                                       'errors': exc.to_dict()})
            except me.NotUniqueError as exc:
                log.error("Record %s not unique error: %s", record, exc)
                raise RecordExistsError()
            # There's a chance that we have received duplicate records as for
            # example for Route NS records, so skip adding it to the list if we
            # already have it
            for rec in records:
                if rec.record_id == record.record_id:
                    records.remove(rec)
                    break
            records.append(record)
        self.cloud.owner.mapper.update(new_records)

        # Then delete any records that are in the DB for this zone but were not
        # returned by the list_records() method meaning the were deleted in the
        # DNS provider.
        Record.objects(zone=zone,
                       id__nin=[r.id for r in records],
                       deleted=None).update(
                           set__deleted=datetime.datetime.utcnow())

        # Format zone information.
        return records

    def _list_records__fetch_records(self, zone_id):
        """Returns all available records on a specific zone. """

        # Try to get the list of DNS records under a specific zone from
        # the provider API.
        # We cannot call list_records() with the zone_id, we need to provide
        # a zone object. We will get that by calling the get_zone() method.
        try:
            records = self.connection.get_zone(zone_id).list_records()
            log.info("List records returned %d results for %s.",
                     len(records), self.cloud)
            return records
        except InvalidCredsError as exc:
            log.warning("Invalid creds on running list_recordss on %s: %s",
                        self.cloud, exc)
            raise CloudUnauthorizedError()
        except ssl.SSLError as exc:
            log.error("SSLError on running list_recordss on %s: %s",
                      self.cloud, exc)
            raise CloudUnavailableError(exc=exc)
        except ZoneDoesNotExistError as exc:
            log.warning("No zone found for %s in: %s ", zone_id, self.cloud)
            raise ZoneNotFoundError(exc=exc)
        except Exception as exc:
            log.exception("Error while running list_records on %s", self.cloud)
            raise CloudUnavailableError(exc=exc)

    def _list_records__postparse_data(self, pr_record, record):
        """Postparse the records returned from the provider"""
        data = pr_record.data
        if pr_record.type == "CNAME":
            if not data.endswith('.'):
                data += '.'
        if data not in record.rdata:
            record.rdata.append(data)

    def delete_record(self, record, expire=False):
        """
        Public method to be called with a zone and record ids to delete the
        specific record under the specified zone.
        """
        self._delete_record__from_id(record.zone.zone_id, record.record_id)
        if expire:
            record.delete()
        else:
            record.update(set__deleted=datetime.datetime.utcnow())

    def _delete_record__from_id(self, zone_id, record_id):
        """
        We use the zone and record ids to delete the specific record under the
        specified zone.
        """
        try:
            self.connection.get_record(zone_id, record_id).delete()
        except ZoneDoesNotExistError as exc:
            log.warning("No zone found for %s in: %s ", zone_id, self.cloud)
            raise ZoneNotFoundError(exc=exc)
        except RecordDoesNotExistError as exc:
            log.warning("No record found for id: %s under zone %s",
                        record_id, zone_id)
            raise RecordNotFoundError(exc=exc)
        except Exception as exc:
            log.exception("Error while running delete_record on %s",
                          self.cloud)
            raise CloudUnavailableError(exc=exc)

    def delete_zone(self, zone, expire=False):
        """
        Public method called to delete the specific zone for the provided id.
        """
        self._delete_zone__for_cloud(zone.zone_id)
        if expire:
            zone.delete()
        else:
            zone.update(set__deleted=datetime.datetime.utcnow())

    def _delete_zone__for_cloud(self, zone_id):
        """
        We use the zone id to retrieve and delete it for this cloud.
        """
        try:
            self.connection.get_zone(zone_id).delete()
        except ZoneDoesNotExistError as exc:
            log.warning("No zone found for %s in: %s ", zone_id, self.cloud)
            raise ZoneNotFoundError(exc=exc)
        except Exception as exc:
            log.exception("Error while running delete_zone on %s", self.cloud)
            raise CloudUnavailableError("Failed to delete zone: %s " % exc)

    def create_zone(self, zone, **kwargs):
        """
        This is the public method that is called to create a new DNS zone.
        """
        self._create_zone__prepare_args(kwargs)
        pr_zone = self._create_zone__for_cloud(**kwargs)
        # Set fields to cloud model and perform early validation.
        zone.zone_id = pr_zone.id
        zone.domain = pr_zone.domain
        zone.type = pr_zone.type
        zone.ttl = pr_zone.ttl
        zone.extra = pr_zone.extra
        # Attempt to save.
        try:
            zone.save()
        except me.ValidationError as exc:
            log.error("Error updating %s: %s", zone, exc.to_dict())
            raise BadRequestError({'msg': exc.message,
                                   'errors': exc.to_dict()})
        except me.NotUniqueError as exc:
            log.error("Zone %s not unique error: %s", zone, exc)
            raise ZoneExistsError()

    def _create_zone__prepare_args(self, kwargs):
        """ This private method to prepare the args for the zone creation."""
        if not kwargs['domain'].endswith('.'):
            kwargs['domain'] += '.'

    def _create_zone__for_cloud(self, **kwargs):
        """
        This is the private method called to create a record under a specific
        zone. The underlying functionality is implement in the same way for
        all available providers so there shouldn't be any reason to override
        this.
        ----
        """
        try:
            zone = self.connection.create_zone(**kwargs)
            log.info("Zone %s created successfully for %s.",
                     zone.domain, self.cloud)
            return zone
        except InvalidCredsError as exc:
            log.warning("Invalid creds on running create_zone on %s: %s",
                        self.cloud, exc)
            raise CloudUnauthorizedError()
        except ssl.SSLError as exc:
            log.error("SSLError on running create_zone on %s: %s",
                      self.cloud, exc)
            raise CloudUnavailableError(exc=exc)
        except Exception as exc:
            log.exception("Error while running create_zone on %s", self.cloud)
            raise ZoneCreationError("Failed to create zone, "
                                    "got error: %s" % exc, exc)

    def create_record(self, record, **kwargs):
        """
        This is the public method that is called to create a new DNS record
        under a specific zone.
        """
        record.name = kwargs['name']
        record.type = kwargs['type']
        if isinstance(kwargs['data'], list):
            record.rdata = kwargs['data']
        else:
            record.rdata = [kwargs['data']]
        record.ttl = kwargs['ttl']

        try:
            record.clean()
        except me.ValidationError as exc:
            log.error("Error validating %s: %s", record, exc.to_dict())
            raise BadRequestError({'msg': exc.message,
                                   'errors': exc.to_dict()})

        self._create_record__prepare_args(record.zone, kwargs)
        pr_record = self._create_record__for_zone(record.zone, **kwargs)
        record.record_id = pr_record.id
        # This is not something that should be given by the user, e.g. we
        # are only using this to store the ttl, so we should onl save this
        # value if it's returned by the provider.
        record.extra = pr_record.extra
        try:
            record.save()
        except me.ValidationError as exc:
            log.error("Error validating %s: %s", record, exc.to_dict())
            raise BadRequestError({'msg': exc.message,
                                   'errors': exc.to_dict()})
        except me.NotUniqueError as exc:
            log.error("Record %s not unique error: %s", record, exc)
            raise RecordExistsError()

    def _create_record__for_zone(self, zone, **kwargs):
        """
        This is the private method called to create a record under a specific
        zone. The underlying functionality is implement in the same way for
        all available providers so there shouldn't be any reason to override
        this.
        ----
        """
        try:
            zone = self.connection.get_zone(zone.zone_id)
            record = zone.create_record(**kwargs)
            log.info("Type %s record created successfully for %s.",
                     record.type, self.cloud)
            return record
        except InvalidCredsError as exc:
            log.warning("Invalid creds on running create_record on %s: %s",
                        self.cloud, exc)
            raise CloudUnauthorizedError()
        except ssl.SSLError as exc:
            log.error("SSLError on running create_record on %s: %s",
                      self.cloud, exc)
            raise CloudUnavailableError(exc=exc)
        except ZoneDoesNotExistError as exc:
            log.warning("No zone found for %s in: %s ", zone.zone_id,
                        self.cloud)
            raise ZoneNotFoundError(exc=exc)
        except Exception as exc:
            log.exception("Error while running create_record on %s",
                          self.cloud)
            raise RecordCreationError("Failed to create record, "
                                      "got error: %s" % exc, exc)

    def _create_record__prepare_args(self, zone, kwargs):
        """
        This is a private method that should be implemented for each specific
        provider depending on how they expect the record data.
        ---
        """
        if kwargs['type'] == 'CNAME':
            kwargs['data'] = kwargs['data'].rstrip('.')
        if kwargs['type'] == 'TXT':
            if not kwargs['data'].endswith('"'):
                kwargs['data'] += '"'
            if not kwargs['data'].startswith('"'):
                kwargs['data'] = '"' + kwargs['data']
        if kwargs['name'].endswith(zone.domain):
            kwargs['name'] = kwargs['name'][:-(len(zone.domain) + 1)]
        kwargs.pop('ttl')

    @staticmethod
    def find_best_matching_zone(owner, name):
        """
        This is a static method that tries to extract a valid domain from
        the name provided, trying to find the best matching DNS zone. This only
        works with 'A', 'AAAA' and 'CNAME' type records.
        This is common for all providers, there's no need to override this.
        ---
        """
        # TODO: Adding here for circular dependency issue. Need to fix this.
        from mist.api.dns.models import Zone

        # Split hostname in dot separated parts.
        parts = [part for part in name.split('.') if part]
        # Find all possible domains for this domain name,
        # longest first
        all_domains = {}
        for i in range(1, len(parts) - 1):
            subdomain = '.'.join(parts[:i])
            domain = '.'.join(parts[i:]) + '.'
            all_domains[domain] = subdomain
        if not all_domains:
            raise BadRequestError("Couldn't extract a valid domain from "
                                  "the provided '%s'." % name)

        zones = Zone.objects(owner=owner)
        # We need to iterate over all the cloud DNS zones to find
        # any that is matching based on the domain. If one is found
        # then create an "A" type record with the provided name.
        for zone_candidate in zones:
            for domain, subdomain in all_domains.iteritems():
                if zone_candidate.domain == domain:
                    return zone_candidate
        raise BadRequestError("No DNS zone found, can't proceed with "
                              "creating record '%s'." % name)
