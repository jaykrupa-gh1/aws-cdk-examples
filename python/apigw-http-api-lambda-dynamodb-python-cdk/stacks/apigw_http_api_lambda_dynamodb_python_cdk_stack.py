# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import os
from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb_,
    aws_lambda as lambda_,
    aws_apigateway as apigw_,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
    aws_cloudwatch as cloudwatch,
    Duration,
)
from constructs import Construct

TABLE_NAME = "demo_table"


class ApigwHttpApiLambdaDynamodbPythonCdkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # VPC
        vpc = ec2.Vpc(
            self,
            "Ingress",
            cidr="10.1.0.0/16",
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Private-Subnet", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24
                )
            ],
        )
        
        # Create VPC endpoint for DynamoDB
        dynamo_db_endpoint = ec2.GatewayVpcEndpoint(
            self,
            "DynamoDBVpce",
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
            vpc=vpc,
        )

        # This allows to customize the endpoint policy
        dynamo_db_endpoint.add_to_policy(
            iam.PolicyStatement(
                principals=[iam.AnyPrincipal()],
                actions=[
                    "dynamodb:DescribeStream",
                    "dynamodb:DescribeTable",
                    "dynamodb:Get*",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:CreateTable",
                    "dynamodb:Delete*",
                    "dynamodb:Update*",
                    "dynamodb:PutItem"
                ],
                resources=["*"],
            )
        )

        # Create VPC endpoint for X-Ray
        xray_endpoint = ec2.InterfaceVpcEndpoint(
            self,
            "XRayVpce",
            service=ec2.InterfaceVpcEndpointAwsService.XRAY,
            vpc=vpc,
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
        )

        # Create VPC endpoint for CloudWatch Logs
        logs_endpoint = ec2.InterfaceVpcEndpoint(
            self,
            "CloudWatchLogsVpce",
            service=ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
            vpc=vpc,
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
        )

        # Create DynamoDb Table with point-in-time recovery
        demo_table = dynamodb_.Table(
            self,
            TABLE_NAME,
            partition_key=dynamodb_.Attribute(
                name="id", type=dynamodb_.AttributeType.STRING
            ),
            point_in_time_recovery=True,
        )

        # Create the Lambda function with log retention and reserved concurrency
        api_hanlder = lambda_.Function(
            self,
            "ApiHandler",
            function_name="apigw_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            code=lambda_.Code.from_asset("lambda/apigw-handler"),
            handler="index.handler",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            memory_size=1024,
            timeout=Duration.minutes(5),
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_YEAR,
            reserved_concurrent_executions=100,
        )

        # grant permission to lambda to write to demo table
        demo_table.grant_write_data(api_hanlder)
        api_hanlder.add_environment("TABLE_NAME", demo_table.table_name)

        # CloudWatch alarm for concurrent executions approaching limit
        cloudwatch.Alarm(
            self,
            "LambdaConcurrencyAlarm",
            metric=api_hanlder.metric_concurrent_executions(),
            threshold=80,
            evaluation_periods=2,
            alarm_description="Lambda concurrent executions approaching reserved limit (80/100)"
        )

        # CloudWatch alarm for throttled invocations
        cloudwatch.Alarm(
            self,
            "LambdaThrottlesAlarm",
            metric=api_hanlder.metric_throttles(),
            threshold=10,
            evaluation_periods=1,
            alarm_description="Lambda function is being throttled"
        )

        # Create log group for API Gateway access logs
        api_log_group = logs.LogGroup(
            self,
            "ApiGatewayAccessLogs",
            retention=logs.RetentionDays.ONE_YEAR,
        )

        # Create API Gateway with X-Ray tracing, access logging, throttling, and API key requirement
        api = apigw_.LambdaRestApi(
            self,
            "Endpoint",
            handler=api_hanlder,
            default_method_options=apigw_.MethodOptions(
                api_key_required=True
            ),
            deploy_options=apigw_.StageOptions(
                throttling_rate_limit=100,
                throttling_burst_limit=200,
                tracing_enabled=True,
                access_log_destination=apigw_.LogGroupLogDestination(api_log_group),
                access_log_format=apigw_.AccessLogFormat.json_with_standard_fields(
                    caller=True,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=True,
                ),
            ),
        )

        # Create usage plan with per-client throttling and quota
        usage_plan = api.add_usage_plan(
            "UsagePlan",
            name="StandardUsagePlan",
            throttle=apigw_.ThrottleSettings(
                rate_limit=50,
                burst_limit=100
            ),
            quota=apigw_.QuotaSettings(
                limit=10000,
                period=apigw_.Period.DAY
            )
        )

        # Create API key
        api_key = api.add_api_key("ApiKey", api_key_name="DefaultApiKey")

        # Associate API key with usage plan
        usage_plan.add_api_key(api_key)

        # Add API stage to usage plan
        usage_plan.add_api_stage(stage=api.deployment_stage)
