#!/usr/bin/env python

from .server import server
from .tunnel import tunnel
from .memory import memory
from .limeerror import limeerror as LimeError
import boto3
from botocore.exceptions import NoCredentialsError


class utility():

    def __init__(self, logger):
        self.logger = logger

    # TODO:(joel) validate config
    def invalid_config(self, config):
        return False

    def transform(self, config):
        multi_config = []
        workers = self.get_worker_count(config)
        log_dir, log_prefix = self.get_log_vars(config)

        for host in config['hosts']:
            try:
                aws_config = config['aws']
            except KeyError:
                aws_config = False

            if aws_config:
                conf = {'host': host,
                        'aws': aws_config}
            else:
                conf = {'host': host}

            conf['logging'] = {'logger': self.logger.name,
                               'dir': log_dir,
                               'prefix': log_prefix}
            multi_config.append(conf)

        return multi_config, workers

    def get_log_vars(self, config):
        try:
            log_dir = config['logging']['dir']
            log_prefix = config['logging']['prefix']
        except KeyError:
            log_dir = ''
            log_prefix = ''
        return log_dir, log_prefix

    def get_worker_count(self, config):
        try:
            workers = int(config['workers'])
        except Exception as e:
            if type(e) == KeyError:
                self.logger.info("no worker count specified. defaulting to 1")
                workers = 1
                pass
            elif type(e) == ValueError:
                if config['workers'] != 'auto':
                    self.logger.info("invalid worker config, " +
                                     "workers must be an integer or auto")
                    raise ValueError('workers must be an integer or "auto"')
                else:
                    workers = config['workers']
                    pass
            else:
                raise
            return workers

    def port_specified(self, host):
        try:
            if host['Port'] is None:
                ret = False
            else:
                ret = True
        except KeyError:
            ret = False
        return ret

    def select_auth_method(self, host):
        if 'keyfile' in host and 'password' in host:
            auth = 'encrypted_key_file'
        elif 'keyfile' in host:
            auth = 'key_file'
        elif 'password' in host:
            auth = 'password'
        else:
            # TODO(joel): raise exception
            self.logger.info('no auth method specified')
            quit()
        return auth

    def select_port(self, host):
        if self.port_specified(host):
            port = int(host['port'])
        else:
            port = 22
        return port

    def establish_tunnel(self, host, port, auth):
        tun = tunnel(host['addr'], port, host['username'], self.logger)
        if auth == 'encrypted_key_file':
            tun.connect_with_encrypted_keyfile(host['keyfile'],
                                               host['password'])
        elif auth == 'key_file':
            tun.connect_with_keyfile(host['keyfile'])
        elif auth == 'password':
            tun.connect_with_password(host['password'])
        else:
            # TODO(joel): raise exception
            self.logger.info('no auth method specified')
            quit()
        return tun

    def establish_remote_session(self, host, port, auth):
        rem = server(host['addr'], port, host['username'], self.logger)
        if auth == 'encrypted_key_file':
            rem.connect_with_encrypted_keyfile(host['keyfile'],
                                               host['password'])
        elif auth == 'key_file':
            rem.connect_with_keyfile(host['keyfile'])
        elif auth == 'password':
            rem.connect_with_password(host['password'])
        else:
            # TODO(joel): raise exception
            self.logger.info('no auth method specified')
            quit()
        return rem

    def install_lime(self, host, remote, tun_port):
        try:
            module_path = host['module']
            kernel_version = remote.get_kernel_version
        except KeyError as e:
            self.logger.info("no lime module defined for {}".format(
                                host['addr']))
            self.logger.info("{}: attempting module lookup from repo".format(
                             host['addr']))
            module_path, kernel_version = remote.get_kernel_module()
            if module_path is None:
                # TODO: (joel) if interactive prompt user for filepath
                raise LimeError("{}: cannot find module for kernel {}".format(
                          host['addr'],
                          kernel_version))
        remote.upload_file(module_path, 'lime.ko')
        cmd = 'sudo insmod ./lime.ko "path=tcp:{} format=lime"'.format(
              tun_port)
        remote.execute_async(cmd)

    def cleanup_lime(self, remote):
        command = 'sudo rmmod lime.ko'
        remote.execute(command)

    def test_credentials(self, bucket=None):
        self.logger.info('bucket configured but no credentials supplied')
        self.logger.info('checking for aws credentials in environment')
        if bucket is None:
            return False
        else:
            client = boto3.client('s3')
            try:
                client.list_objects(Bucket=bucket)
                self.logger.info('credentials found, proceeding')
                return True
            except NoCredentialsError as e:
                self.logger.info('no credentials found, falling back to file download')
                return False


    def dump_memory(self, config, host, tunnel, remote, tun_port, draw_pbar):
        tunnel.start(tun_port, '127.0.0.1', tun_port)
        lime_loaded = remote.wait_for_lime(port=tun_port)
        memsize = remote.get_mem_size()

        if lime_loaded:
            tun_host = '127.0.0.1'
            remote_host = host['addr']
            mem = memory(tun_host, tun_port, remote_host, memsize,
                                self.logger, draw_pbar)
            try:
                bucket = config['aws']['bucket']
                key = config['aws']['key']
                secret = config['aws']['secret']
                credentials_found = True
            except KeyError as e:
                credentials_found = self.test_credentials(bucket)
                key = None
                secret = None

            filename = '{}-mem.lime'.format(host['addr'])

            #if bucket is not None and key is not None and secret is not None:
            if bucket is not None and credentials_found:
                self.logger.info('{} dumping memory to s3:///{}/{}'.format(
                                 host['addr'],
                                 bucket,
                                 filename))

                mem.to_s3(key_id=key,
                          secret_key=secret,
                          bucket=bucket,
                          filename=filename)
            else:
                self.logger.info('{} dumping memory to {}'.format(host['addr'],
                                                                  filename))
                mem.to_file(filename)
        else:
            self.logger.info("{} Lime failed to load ... exiting".format(
                             host['addr']))
            return False
        remote.execute('sudo rmmod lime.ko')
        tunnel.cleanup()
        return True
