import importlib
import json
import numpy as np
import os
import sys
import time
import uuid
import copy

from hypergan.discriminators import *
from hypergan.distributions import *
from hypergan.generators import *
from hypergan.inputs import *
from hypergan.samplers import *
from hypergan.trainers import *

import hyperchamber as hc
from hyperchamber import Config
from hypergan.ops import TensorflowOps
import tensorflow as tf
import hypergan as hg

from hypergan.gan_component import ValidationException, GANComponent
from ..base_gan import BaseGAN

from hypergan.distributions.uniform_distribution import UniformDistribution
from hypergan.trainers.experimental.consensus_trainer import ConsensusTrainer

class AlignedAliGAN7(BaseGAN):
    """ 
    """
    def __init__(self, *args, **kwargs):
        BaseGAN.__init__(self, *args, **kwargs)

    def required(self):
        """
        `input_encoder` is a discriminator.  It encodes X into Z
        `discriminator` is a standard discriminator.  It measures X, reconstruction of X, and G.
        `generator` produces two samples, input_encoder output and a known random distribution.
        """
        return "generator discriminator ".split()

    def create(self):
        config = self.config
        ops = self.ops

        with tf.device(self.device):
            #x_input = tf.identity(self.inputs.x, name='input')
            xa_input = tf.identity(self.inputs.xa, name='xa_i')
            xb_input = tf.identity(self.inputs.xb, name='xb_i')

            if config.same_g:
                ga = self.create_component(config.generator, input=xb_input, name='a_generator')
                gb = hc.Config({"sample":ga.reuse(xa_input),"controls":{"z":ga.controls['z']}, "reuse": ga.reuse})
            else:
                ga = self.create_component(config.generator, input=xb_input, name='a_generator')
                gb = self.create_component(config.generator, input=xa_input, name='b_generator')

            za = ga.controls["z"]
            zb = gb.controls["z"]

            self.uniform_sample = ga.sample

            xba = ga.sample
            xab = gb.sample
            xa_hat = ga.reuse(gb.sample)
            xb_hat = gb.reuse(ga.sample)

            z_shape = self.ops.shape(za)
            uz_shape = z_shape
            uz_shape[-1] = uz_shape[-1] // len(config.z_distribution.projections)
            ue = UniformDistribution(self, config.z_distribution, output_shape=uz_shape)
            ue2 = UniformDistribution(self, config.z_distribution, output_shape=uz_shape)
            ue3 = UniformDistribution(self, config.z_distribution, output_shape=uz_shape)
            ue4 = UniformDistribution(self, config.z_distribution, output_shape=uz_shape)
            print('ue', ue.sample)

            zua = ue.sample
            zub = ue2.sample

            ga2 = self.create_component(config.generator, input=tf.zeros_like(xb_input), name='ua_generator')
            gb2 = self.create_component(config.generator, input=tf.zeros_like(xb_input), name='ub_generator')
            uga = ga2.reuse(tf.zeros_like(xb_input), replace_controls={"z":zua})
            ugb = gb2.reuse(tf.zeros_like(xb_input), replace_controls={"z":zub})

            ugbprime = gb.reuse(uga)
            ugbzb = gb.controls['z']

            xa = xa_input
            xb = xb_input

            t0 = xb
            t1 = gb.sample
            t2 = ugbprime
            f0 = za
            f1 = zb
            f2 = ugbzb
            stack = [t0, t1, t2]
            stacked = ops.concat(stack, axis=0)
            features = ops.concat([f0, f1, f2], axis=0)

            xa_hat = ugbprime

            d = self.create_component(config.discriminator, name='d_ab', input=stacked, features=[features])
            l = self.create_loss(config.loss, d, xa_input, ga.sample, len(stack))
            loss1 = l
            d_loss1 = l.d_loss
            g_loss1 = l.g_loss

            d_vars1 = d.variables()
            g_vars1 = ga2.variables() + gb2.variables() + gb.variables()+ga.variables()#gb.variables()# + gb.variables()

            d_loss = l.d_loss
            g_loss = l.g_loss

            metrics = {
                    'g_loss': l.g_loss,
                    'd_loss': l.d_loss
                }

            g_vars2 = ga.variables()
            trainers = []

            if config.cyc:
                t0 = xa
                t1 = ga.sample
                f0 = zb
                f1 = za
                stack = [t0, t1]
                stacked = ops.concat(stack, axis=0)
                features = ops.concat([f0, f1], axis=0)

                d = self.create_component(config.discriminator, name='d2_ab', input=stacked, features=[features])
                l = self.create_loss(config.loss, d, xa_input, ga.sample, len(stack))

                d_loss1 += l.d_loss
                g_loss1 += l.g_loss
                d_vars1 += d.variables()
                metrics["gloss2"]=l.g_loss
                metrics["dloss2"]=l.d_loss
                #loss2=l
                #g_loss2 = loss2.g_loss
                #d_loss2 = loss2.d_loss

            if(config.alpha):
                t0 = zub
                t1 = zb
                netzd = tf.concat(axis=0, values=[t0,t1])
                z_d = self.create_component(config.z_discriminator, name='z_discriminator', input=netzd)

                loss3 = self.create_component(config.loss, discriminator = z_d, x=xa_input, generator=ga, split=2)
                d_vars1 += z_d.variables()
                metrics["za_gloss"]=loss3.g_loss
                metrics["za_dloss"]=loss3.d_loss
                d_loss1 += loss3.d_loss
                g_loss1 += loss3.g_loss


            if(config.ug):
                t0 = xb
                t1 = ugb
                netzd = tf.concat(axis=0, values=[t0,t1])
                z_d = self.create_component(config.discriminator, name='ug_discriminator', input=netzd)

                loss3 = self.create_component(config.loss, discriminator = z_d, x=xa_input, generator=ga, split=2)
                d_vars1 += z_d.variables()
                metrics["za_gloss"]=loss3.g_loss
                metrics["za_dloss"]=loss3.d_loss
                d_loss1 += loss3.d_loss
                g_loss1 += loss3.g_loss

            if(config.alpha2):
                t0 = zua
                t1 = za
                netzd = tf.concat(axis=0, values=[t0,t1])
                z_d = self.create_component(config.z_discriminator, name='z_discriminator2', input=netzd)

                loss3 = self.create_component(config.loss, discriminator = z_d, x=xa_input, generator=ga, split=2)
                d_vars1 += z_d.variables()
                metrics["za_gloss"]=loss3.g_loss
                metrics["za_dloss"]=loss3.d_loss
                d_loss1 += loss3.d_loss
                g_loss1 += loss3.g_loss

            if config.ug2:
                t0 = xa
                t1 = uga
                netzd = tf.concat(axis=0, values=[t0,t1])
                z_d = self.create_component(config.discriminator, name='ug_discriminator2', input=netzd)

                loss3 = self.create_component(config.loss, discriminator = z_d, x=xa_input, generator=ga, split=2)
                d_vars1 += z_d.variables()
                metrics["za_gloss"]=loss3.g_loss
                metrics["za_dloss"]=loss3.d_loss
                d_loss1 += loss3.d_loss
                g_loss1 += loss3.g_loss

            if config.ug3:
                t0 = tf.concat(values=[xa,xb], axis=3)
                t1 = tf.concat(values=[uga,ugb], axis=3)
                netzd = tf.concat(axis=0, values=[t0,t1])
                z_d = self.create_component(config.discriminator, name='ug_discriminator3', input=netzd)

                loss3 = self.create_component(config.loss, discriminator = z_d, x=xa_input, generator=ga, split=2)
                d_vars1 += z_d.variables()
                metrics["za_gloss"]=loss3.g_loss
                metrics["za_dloss"]=loss3.d_loss
                d_loss1 += loss3.d_loss
                g_loss1 += loss3.g_loss





            lossa = hc.Config({'sample': [d_loss1, g_loss1], 'metrics': metrics})
            #lossb = hc.Config({'sample': [d_loss2, g_loss2], 'metrics': metrics})
            trainers += [ConsensusTrainer(self, config.trainer, loss = lossa, g_vars = g_vars1, d_vars = d_vars1)]
            #trainers += [ConsensusTrainer(self, config.trainer, loss = lossb, g_vars = g_vars2, d_vars = d_vars2)]
            trainer = MultiTrainerTrainer(trainers)
            self.session.run(tf.global_variables_initializer())

        self.trainer = trainer
        self.generator = ga
        self.encoder = gb # this is the other gan
        self.uniform_distribution = hc.Config({"sample":zb})#uniform_encoder
        self.zb = zb
        self.z_hat = gb.sample
        self.x_input = xa_input
        self.autoencoded_x = xa_hat

        self.cyca = xa_hat
        self.cycb = xb_hat
        self.xba = xba
        self.xab = xab
        self.uga = uga
        self.ugb = ugb

        rgb = tf.cast((self.generator.sample+1)*127.5, tf.int32)
        self.generator_int = tf.bitwise.bitwise_or(rgb, 0xFF000000, name='generator_int')


    def create_loss(self, loss_config, discriminator, x, generator, split):
        loss = self.create_component(loss_config, discriminator = discriminator, x=x, generator=generator, split=split)
        return loss

    def create_encoder(self, x_input, name='input_encoder'):
        config = self.config
        input_encoder = dict(config.input_encoder or config.g_encoder or config.generator)
        encoder = self.create_component(input_encoder, name=name, input=x_input)
        return encoder

    def create_z_discriminator(self, z, z_hat):
        config = self.config
        z_discriminator = dict(config.z_discriminator or config.discriminator)
        z_discriminator['layer_filter']=None
        net = tf.concat(axis=0, values=[z, z_hat])
        encoder_discriminator = self.create_component(z_discriminator, name='z_discriminator', input=net)
        return encoder_discriminator

    def create_cycloss(self, x_input, x_hat):
        config = self.config
        ops = self.ops
        distance = config.distance or ops.lookup('l1_distance')
        pe_layers = self.gan.skip_connections.get_array("progressive_enhancement")
        cycloss_lambda = config.cycloss_lambda
        if cycloss_lambda is None:
            cycloss_lambda = 10
        
        if(len(pe_layers) > 0):
            mask = self.progressive_growing_mask(len(pe_layers)//2+1)
            cycloss = tf.reduce_mean(distance(mask*x_input,mask*x_hat))

            cycloss *= mask
        else:
            cycloss = tf.reduce_mean(distance(x_input, x_hat))

        cycloss *= cycloss_lambda
        return cycloss


    def create_z_cycloss(self, z, x_hat, encoder, generator):
        config = self.config
        ops = self.ops
        total = None
        distance = config.distance or ops.lookup('l1_distance')
        if config.z_hat_lambda:
            z_hat_cycloss_lambda = config.z_hat_cycloss_lambda
            recode_z_hat = encoder.reuse(x_hat)
            z_hat_cycloss = tf.reduce_mean(distance(z_hat,recode_z_hat))
            z_hat_cycloss *= z_hat_cycloss_lambda
        if config.z_cycloss_lambda:
            recode_z = encoder.reuse(generator.reuse(z))
            z_cycloss = tf.reduce_mean(distance(z,recode_z))
            z_cycloss_lambda = config.z_cycloss_lambda
            if z_cycloss_lambda is None:
                z_cycloss_lambda = 0
            z_cycloss *= z_cycloss_lambda

        if config.z_hat_lambda and config.z_cycloss_lambda:
            total = z_cycloss + z_hat_cycloss
        elif config.z_cycloss_lambda:
            total = z_cycloss
        elif config.z_hat_lambda:
            total = z_hat_cycloss
        return total



    def input_nodes(self):
        "used in hypergan build"
        if hasattr(self.generator, 'mask_generator'):
            extras = [self.mask_generator.sample]
        else:
            extras = []
        return extras + [
                self.x_input
        ]


    def output_nodes(self):
        "used in hypergan build"

    
        if hasattr(self.generator, 'mask_generator'):
            extras = [
                self.mask_generator.sample, 
                self.generator.g1x,
                self.generator.g2x
            ]
        else:
            extras = []
        return extras + [
                self.encoder.sample,
                self.generator.sample, 
                self.uniform_sample,
                self.generator_int
        ]
