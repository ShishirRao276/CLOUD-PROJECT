import os
import time
import sys
import socket
import string

import boto
import boto.ec2
import boto.ec2.elb
import boto.ec2.autoscale
import boto.ec2.cloudwatch

from boto.ec2.autoscale import AutoScaleConnection
from boto.ec2.autoscale import LaunchConfiguration
from boto.ec2.autoscale import AutoScalingGroup
from boto.ec2.autoscale import ScalingPolicy

from boto.ec2.cloudwatch import MetricAlarm

# This just works out BASE_PATH as the absolute path of ../ relative to this script location
# We use this to add the "include" directory for local module includes during development/experimentation
BASE_PATH = reduce (lambda l, r: l + os.path.sep + r, os.path.dirname(os.path.realpath(__file__)).split(os.path.sep)[:-1])
sys.path.append(os.path.join(BASE_PATH, "include"))

#### Start parameter block ####

collectorLoadBalancerName = 'StormTestCollector'

# Define the region we're dealing with.
thisRegion = 'eu-west-1'

# Instance check, behind the load balancer
healthCheckPort = '80'

# Configuration parameters for the different tiers

# AMI to use for the Collector Tier 
collectorTierAMI = 'ami-93b087e7'
collectorTierAccessKey = 'default-TVT-ec2key'
collectorTierSecurityGroups = ['StormTest-Collector-WebApp']
collectorTierInstanceType = 't1.micro'
collectorTierExtraEBSStorage = '100'
collectorTierUserData = ''

# AMI to use for the Ingest Tier

# AMI to use for the Storage/Analysis Tier


#### End parameter block ####

# Proxy check for the S3 VLAN1 network

proxyHost = None
proxyPort = None

# Local proxy setting determination

myAddress = socket.gethostbyaddr(socket.gethostname())

if string.find(myAddress[0], "s3group") > 0:
    if string.find(myAddress[-1][0], "193.120") == 0:
        proxyHost = 'w3proxy.s3group.com'
        proxyPort = 3128


# Turn on verbose output: debugging aid
boto.set_stream_logger('mylog')

###########################################################################
# Stage #1: Load balancer
###########################################################################

# Takes credentials from the environment : assumes they are defined in shell variables in one of the supported ways
# If they aren't this will fail authentication
reg_con = boto.ec2.connect_to_region(region_name=thisRegion, proxy=proxyHost, proxy_port=proxyPort)

# We will allow all availability zones in the chosen region. 
# This is supposed to allow a better chance of the site staying up (although I am not sure it makes any difference)
allZones = reg_con.get_all_zones()

zoneStrings = []
for zone in allZones:
    zoneStrings.append(zone.name)

elb_con = boto.ec2.elb.connect_to_region(region_name=thisRegion, proxy=proxyHost, proxy_port=proxyPort)

# Create some HealthChecks to check the ports we're going to open
hcHTTP = boto.ec2.elb.HealthCheck(access_point='httpCheck',
                      interval=30,
                      target='TCP:' + healthCheckPort,
                      healthy_threshold=3,
                      timeout=2,
                      unhealthy_threshold=5) 

# Create a load balancer for ports 80 and 443 (in reality you probably only need one or the other)
# Deliberately using TCP here, not HTTP
loadBalancer = elb_con.create_load_balancer(collectorLoadBalancerName, zoneStrings, [(80, 80, 'tcp'), (443, 443, 'tcp')])

# Add the health check for the instances
loadBalancer.configure_health_check(hcHTTP)

print "\nLoad Balancer created: map CNAME to " + loadBalancer.dns_name

################################################################################
# Stage #2: Launch configuration and Auto-scaling group setup, Collector Tier
################################################################################
as_con = boto.ec2.autoscale.connect_to_region(region_name=thisRegion, proxy=proxyHost, proxy_port=proxyPort)

# Next, setup and create a launch configuration

# Some notes on our launch configuration:
# We use a hard-coded AMI (from our parameter block above)
# Any standard technique could be used to search for the AMI to run 
# e.g. see launchStorm.py where we fetch a list of all private images
#
# We use the defined access key, security group(s) and instance types from the parameter block
#
# We create (and attach) a volume to /dev/sdh. It is implicitly delete_on_termination, there
# does not seem to be a way (http://autoscaling.amazonaws.com/doc/2010-08-01/AutoScaling.wsdl) to control this.
#
# We also attempt to attach one of the ephemeral devices. This will work if you're not using a micro instance and do nothing otherwise.
#
# NB: This required a change to __init__.py in boto\ec2\autoscale to support nested dictionaries in the blockDeviceMap

blockDeviceMap = []
blockDeviceMap.append( {'DeviceName':'/dev/sdc', 'VirtualName' : 'ephemeral0'})
blockDeviceMap.append( {'DeviceName':'/dev/sdh', 'Ebs': {'VolumeSize' : '100'} })

# Setup and create our launch configuration tier, describing what to start when the auto-scaling group
# decides that scaling is required 

collectorTierLaunchConfig = LaunchConfiguration(name='ctLaunchConfig',
                                                image_id=collectorTierAMI,
                                                key_name=collectorTierAccessKey,
                                                security_groups=collectorTierSecurityGroups,
                                                instance_type=collectorTierInstanceType,
                                                block_device_mappings=blockDeviceMap)

as_con.create_launch_configuration(collectorTierLaunchConfig)

# Create an Autoscaling group, associate the launch configuration we just made with that group, and tie this to
# our load balancer created above

collectorTierScalingGroup = AutoScalingGroup(name='ctScalingGroup',
                                             availability_zones=zoneStrings,
                                             default_cooldown=300,
                                             desired_capacity=2,
                                             min_size=2,
                                             max_size=6,
                                             load_balancers=[collectorLoadBalancerName],
                                             launch_config=collectorTierLaunchConfig)

as_con.create_auto_scaling_group(collectorTierScalingGroup)

# Now create the scaling conditions, under which the scaling will take place
# This is a two-stage process: create a scaleUp and scaleDown policy, and then tie these to metrics
# which we will fetch using the CloudWatch monitoring services

collectionTierScalingUpPolicy = ScalingPolicy(name='ctScaleUp',
                                              adjustment_type='ChangeInCapacity',
                                              as_name=collectorTierScalingGroup.name,
                                              scaling_adjustment=2,
                                              cooldown=180)

collectionTierScalingDownPolicy = ScalingPolicy(name='ctScaleDown',
                                              adjustment_type='ChangeInCapacity',
                                              as_name=collectorTierScalingGroup.name,
                                              scaling_adjustment=-1,
                                              cooldown=180)

as_con.create_scaling_policy(collectionTierScalingUpPolicy)
as_con.create_scaling_policy(collectionTierScalingDownPolicy)

# It appears that we need to fetch the policies again, to make sure that the policy_arn is filled in for each policy
# We need the policy_arn to set the scaling alarm below
# Not sure if there is a way to avoid this? Should as_con.create_scaling_policy fill this in somehow?

policyResults = as_con.get_all_policies(as_group=collectorTierScalingGroup.name, policy_names=[collectionTierScalingUpPolicy.name])
collectionTierScalingUpPolicy = policyResults[0]

policyResults = as_con.get_all_policies(as_group=collectorTierScalingGroup.name, policy_names=[collectionTierScalingDownPolicy.name])
collectionTierScalingDownPolicy = policyResults[0]

# Create the connection to the CloudWatch endpoint
cw_con = boto.ec2.cloudwatch.connect_to_region(region_name=thisRegion, proxy=proxyHost, proxy_port=proxyPort)

# Create the two alarms, tie them to the Policies created above
# NB This also required some changes to cloudwatch alarm.py and __init__.py, to support passing of extra parameters to MetricAlarm (seems sensible) and also
#    to support building the dimensions arguments 

# Note you have to be careful in the parameters to pass to MetricAlarm: create_alarm() does not do much error checking on Amazon's side and it will happily allow you to create 
# an "impossible" alarm which will do nothing: for example, you can make a CPUUtilization alarm in namespace AWS/ELB. This will appear in your CloudWatch console.
# It will just do absolutely nothing ...

alarm_actions = []
alarm_actions.append(collectionTierScalingUpPolicy.policy_arn)

dimensions = {"AutoScalingGroupName" : collectorTierScalingGroup.name}

collectionTierUpAlarm = MetricAlarm(name='ctScaleAlarm-HighCPU',
                                    namespace='AWS/EC2',
                                    metric='CPUUtilization' ,
                                    statistic='Average',
                                    comparison='>',
                                    threshold='70',
                                    period='60',
                                    evaluation_periods=2,
                                    actions_enabled=True,
                                    alarm_action=alarm_actions,
                                    dimensions=dimensions)

alarm_actions = []
alarm_actions.append(collectionTierScalingDownPolicy.policy_arn)

collectionTierDownAlarm = MetricAlarm(name='ctScaleAlarm-LowCPU',
                                      namespace='AWS/EC2',
                                      metric='CPUUtilization' ,
                                      statistic='Average',
                                      comparison='<=',
                                      threshold='30',
                                      period='60',
                                      evaluation_periods=2,
                                      actions_enabled=True,
                                      alarm_action=alarm_actions,
                                      dimensions=dimensions)

cw_con.create_alarm(collectionTierUpAlarm)
cw_con.create_alarm(collectionTierDownAlarm)