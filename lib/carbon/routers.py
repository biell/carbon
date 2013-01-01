from carbon.relayrules import loadRelayRules
from carbon.hashing import ConsistentHashRing
from carbon import log, util


class DatapointRouter:
  "Interface for datapoint routing logic implementations"

  def addDestination(self, destination):
    "destination is a (host, port, instance) triple"

  def removeDestination(self, destination):
    "destination is a (host, port, instance) triple"

  def getDestinations(self, key):
    """Generate the destinations where the given routing key should map to. Only
    destinations which are configured (addDestination has been called for it)
    may be generated by this method."""


class RelayRulesRouter(DatapointRouter):
  def __init__(self, config_file='relay-rules.conf'):
    self.rules = loadRelayRules(config_file)
    self.destinations = set()

  def addDestination(self, destination):
    self.destinations.add(destination)

  def removeDestination(self, destination):
    self.destinations.discard(destination)

  def getDestinations(self, key):
    for rule in self.rules:
      if rule.matches(key):
        for destination in rule.destinations:
          if destination in self.destinations:
            yield destination
          else:
            log.msg("relay-rule destination \"" + str(destination) + "\" "
                    "not configured in relay.conf DESTINATIONS")
        if not rule.continue_matching:
          return


class ConsistentHashingRouter(DatapointRouter):
  def __init__(self, replication_factor=1):
    self.replication_factor = int(replication_factor)
    self.instance_ports = {} # { (server, instance) : port }
    self.ring = ConsistentHashRing([])

  def addDestination(self, destination):
    (server, port, instance) = destination
    if (server, instance) in self.instance_ports:
      raise Exception("destination instance (%s, %s) already configured" % (server, instance))
    self.instance_ports[ (server, instance) ] = port
    self.ring.add_node( (server, instance) )

  def removeDestination(self, destination):
    (server, port, instance) = destination
    if (server, instance) not in self.instance_ports:
      raise Exception("destination instance (%s, %s) not configured" % (server, instance))
    del self.instance_ports[ (server, instance) ]
    self.ring.remove_node( (server, instance) )

  def getDestinations(self, metric):
    key = self.getKey(metric)

    for count,node in enumerate(self.ring.get_nodes(key)):
      if count == self.replication_factor:
        return
      (server, instance) = node
      port = self.instance_ports[ (server, instance) ]
      yield (server, port, instance)

  def getKey(self, metric):
    return metric

  def setKeyFunction(self, func):
    self.getKey = func

  def setKeyFunctionFromModule(self, keyfunc_spec):
    module_path, func_name = keyfunc_spec.rsplit(':', 1)
    keyfunc = util.load_module(module_path, member=func_name)
    self.setKeyFunction(keyfunc)

class AggregatedConsistentHashingRouter(DatapointRouter):
  def __init__(self, agg_rules_manager, replication_factor=1):
    self.hash_router = ConsistentHashingRouter(replication_factor)
    self.agg_rules_manager = agg_rules_manager

  def addDestination(self, destination):
    self.hash_router.addDestination(destination)

  def removeDestination(self, destination):
    self.hash_router.removeDestination(destination)

  def getDestinations(self, key):
    # resolve metric to aggregate forms
    resolved_metrics = []
    for rule in self.agg_rules_manager.rules:
      aggregate_metric = rule.get_aggregate_metric(key)
      if aggregate_metric is None:
        continue
      else:
        resolved_metrics.append(aggregate_metric)

    # if the metric will not be aggregated, send it raw
    # (will pass through aggregation)
    if len(resolved_metrics) == 0:
      resolved_metrics.append(key)

    # get consistent hashing destinations based on aggregate forms
    destinations = set()
    for resolved_metric in resolved_metrics:
      for destination in self.hash_router.getDestinations(resolved_metric):
        destinations.add(destination)

    for destination in destinations:
      yield destination
