if user_data != '':
security_groups=list('sg-d73fc5b2')

print "Trying to use this AMI [%s]" % image_ami

lc = LaunchConfiguration(
  name=launch_config_name,
  image_id=image_ami,
  key_name=env.aws_key_name,
  security_groups=security_groups,
  instance_type=instance_type
)

launch_config = autoscale_conn.create_launch_configuration(lc)