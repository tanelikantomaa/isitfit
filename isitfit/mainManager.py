import boto3
import pandas as pd
from tqdm import tqdm
import datetime as dt
import numpy as np
import pytz

import logging
logger = logging.getLogger('isitfit')


from .utils import mergeSeriesOnTimestampRange, ec2_catalog
from .cloudtrail_ec2type import Manager as CloudtrailEc2typeManager


SECONDS_IN_ONE_DAY = 60*60*24 # 86400  # used for granularity (daily)
N_DAYS=90



class MainManager:
    def __init__(self):
        self.ec2_resource = boto3.resource('ec2')
        self.cloudwatch_resource = boto3.resource('cloudwatch')

        dt_now_d=dt.datetime.now().replace(tzinfo=pytz.utc)
        self.StartTime=dt_now_d - dt.timedelta(days=N_DAYS)
        self.EndTime=dt_now_d
        logger.debug("Metrics start..end: %s .. %s"%(self.StartTime, self.EndTime))

        self.cloudtrail_client = boto3.client('cloudtrail')
        self.cloudtrail_manager = CloudtrailEc2typeManager(self.cloudtrail_client, dt_now_d)


    def get_ifi(self):
        # download ec2 catalog: 2 columns: ec2 type, ec2 cost per hour
        logger.debug("Downloading ec2 catalog")
        self.df_cat = ec2_catalog()

        # 0th pass to count
        n_ec2 = len(list(self.ec2_resource.instances.all()))

        # get cloudtail ec2 type changes for all instances
        self.cloudtrail_manager.init_data(self.ec2_resource.instances.all(), n_ec2)

        # iterate over all ec2 instances
        sum_capacity = 0
        sum_used = 0
        df_all = []
        for ec2_obj in tqdm(self.ec2_resource.instances.all(), total=n_ec2, desc="Second pass, EC2 instance", initial=1):
            res_capacity, res_used = self._handle_ec2obj(ec2_obj)
            sum_capacity += res_capacity
            sum_used += res_used
            df_all.append({'instance_id': ec2_obj.instance_id, 'capacity': res_capacity, 'used': res_used})
            logger.debug("\n")

        # for debugging
        df_all = pd.DataFrame(df_all)
        logger.debug("\ncapacity/used per instance")
        logger.debug(df_all)
        logger.debug("\n")

        if sum_capacity==0: return 0

        return sum_used/sum_capacity*100


    def _cloudwatch_metrics(self, ec2_obj):
        """
        Return a pandas series of CPU utilization, daily max, for 90 days
        """
        metrics_iterator = self.cloudwatch_resource.metrics.filter(
            Namespace='AWS/EC2', 
            MetricName='CPUUtilization', 
            Dimensions=[{'Name': 'InstanceId', 'Value': ec2_obj.instance_id}]
          )
        df_cw1 = []
        for m_i in metrics_iterator:
            json_i = m_i.get_statistics(
              Dimensions=[{'Name': 'InstanceId', 'Value': ec2_obj.instance_id}],
              Period=SECONDS_IN_ONE_DAY,
              Statistics=['Average', 'SampleCount'],
              Unit='Percent',
              StartTime=self.StartTime,
              EndTime=self.EndTime
            )
            # logger.debug(json_i)
            if len(json_i['Datapoints'])==0: continue # skip (no data)

            df_i = pd.DataFrame(json_i['Datapoints'])
            df_i = df_i[['Timestamp', 'SampleCount', 'Average']]
            df_i = df_i.sort_values(['Timestamp'], ascending=True)
            df_cw1.append(df_i)

        #
        if len(df_cw1)==0:
          # raise ValueError("No metrics for %s"%ec2_obj.instance_id)
          return None

        if len(df_cw1) >1:
          raise ValueError(">1 # metrics for %s"%ec2_obj.instance_id)

        # merge
        # df_cw2 = pd.concat(df_cw1, axis=1)

        # done
        # return df_cw2.CPUUtilization
        return df_cw1[0]


    def _handle_ec2obj(self, ec2_obj):
        logger.debug("%s, %s"%(ec2_obj.instance_id, ec2_obj.instance_type))

        # pandas series of CPU utilization, daily max, for 90 days
        df_metrics = self._cloudwatch_metrics(ec2_obj)

        # no data
        if df_metrics is None:
          logger.debug("No cloudwatch")
          return 0, 0

        # pandas series of number of cpu's available on the machine over time, past 90 days
        df_type_ts1 = self.cloudtrail_manager.single(ec2_obj)
        if df_type_ts1 is None:
          logger.debug("No cloudtrail")
          return 0,0

        # convert type timeseries to the same timeframes as pcpu and n5mn
        #if ec2_obj.instance_id=='i-069a7808addd143c7':
        #  import pdb
        #  pdb.set_trace()
        ec2_df = mergeSeriesOnTimestampRange(df_metrics, df_type_ts1)
        logger.debug("\nafter merge series on timestamp range")
        logger.debug(ec2_df.head())

        # merge with type changes (can't use .merge on timestamps, need to use .concat)
        #ec2_df = df_metrics.merge(df_type_ts2, left_on='Timestamp', right_on='EventTime', how='left')
        # ec2_df = pd.concat([df_metrics, df_type_ts2], axis=1)

        # merge with catalog
        ec2_df = ec2_df.merge(self.df_cat, left_on='instanceType', right_on='API Name', how='left')
        logger.debug("\nafter merge with catalog")
        logger.debug(ec2_df.head())

        # results: 2 numbers: capacity (USD), used (USD)
        ec2_df['nhours'] = np.ceil(ec2_df.SampleCount/12)
        res_capacity = (ec2_df.nhours*ec2_df.cost_hourly).sum()
        res_used     = (ec2_df.nhours*ec2_df.cost_hourly*ec2_df.Average/100).sum()

        logger.debug("res_capacity=%s, res_used=%s"%(res_capacity, res_used))
        return res_capacity, res_used