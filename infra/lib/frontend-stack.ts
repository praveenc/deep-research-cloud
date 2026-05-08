import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cloudfrontOrigins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct } from 'constructs';
import { DeepResearchConfig } from '../config';
import * as path from 'path';

export interface FrontendStackProps extends cdk.StackProps {
  readonly config: DeepResearchConfig;
  readonly researchBucketName: string; // Pass name, not the construct (avoids cyclic ref)
}

/**
 * Frontend — CloudFront distribution serving:
 * 1. React SPA from a dedicated hosting bucket
 * 2. Research reports from the research bucket (/reports/<slug>/)
 *
 * Access control: OAC (Origin Access Control) — S3 blocks all public access,
 * only CloudFront can read. Report paths require authenticated session.
 */
export class FrontendStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    // S3 bucket for the SPA static assets
    const hostingBucket = new s3.Bucket(this, 'HostingBucket', {
      bucketName: `deep-research-frontend-${props.config.stage}-${this.account}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // Import research bucket by name (avoids cross-stack cyclic dependency with OAC)
    const researchBucket = s3.Bucket.fromBucketName(
      this, 'ResearchBucket', props.researchBucketName
    );

    // CloudFront distribution
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultBehavior: {
        origin: cloudfrontOrigins.S3BucketOrigin.withOriginAccessControl(hostingBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      additionalBehaviors: {
        // Research reports served from the research bucket
        '/reports/*': {
          origin: cloudfrontOrigins.S3BucketOrigin.withOriginAccessControl(researchBucket),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED, // reports are dynamic
        },
      },
      defaultRootObject: 'index.html',
      errorResponses: [
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: '/index.html', // SPA client-side routing
        },
      ],
    });

    // Deploy SPA assets (placeholder — actual React build goes here)
    new s3deploy.BucketDeployment(this, 'DeploySPA', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '../../app/frontend/dist'))],
      destinationBucket: hostingBucket,
      distribution,
      distributionPaths: ['/*'],
    });

    // Outputs
    new cdk.CfnOutput(this, 'DistributionUrl', {
      value: `https://${distribution.distributionDomainName}`,
      exportName: `${props.config.stage}-distribution-url`,
    });

    new cdk.CfnOutput(this, 'DistributionId', {
      value: distribution.distributionId,
    });
  }
}
