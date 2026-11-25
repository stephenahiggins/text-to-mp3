#import <React/RCTBridgeModule.h>
#import <React/RCTEventEmitter.h>
@interface RCT_EXTERN_MODULE(ReadingShareQueue, RCTEventEmitter)
RCT_EXTERN_METHOD(setActive:(BOOL)active)
RCT_EXTERN_METHOD(present)
RCT_EXTERN_METHOD(nextCloudRecording:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(acknowledgeCloudRecording:(NSString *)identifier workspace:(NSString *)workspace resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
@end
