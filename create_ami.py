accessKeyId = "..."
accessKeySecret = "..."
target = "..."

def resolveIp(target):
    import socket
    ip = repr(socket.gethostbyname_ex(target)[2][0])
    return ip

def find_target(target, connection) :
    ip = resolveIp(target)
    print "Finding instance for " + target + " (IP " + ip + ")"
    reservations = connection.get_all_instances();
    for reservation in reservations:
        instances = reservation.instances
        if len(instances) != 1:
            print "Skipping reservation " + reservation
            continue
        instance = instances[0]
        instanceIp = resolveIp(instance.dns_name)
        if instanceIp == ip:
            return instance

    raise Exception("Can't find instance with IP " + ip)

from boto.ec2.connection import EC2Connection

print "Connecting to EC2"
conn = EC2Connection(accessKeyId, accessKeySecret)
print "Connected to EC2"

instance = find_target(target, conn)
print "Backing up instance '{}'".format(instance)