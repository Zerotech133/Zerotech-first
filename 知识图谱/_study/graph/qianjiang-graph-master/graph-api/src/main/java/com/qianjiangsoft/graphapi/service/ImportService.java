package com.qianjiangsoft.graphapi.service;

/**
 * @author marshallwu
 * @date 2022/4/12 16:15
 */
public interface ImportService {

    void importExampleData();

    void importRandomTestData(Integer templateId,long sampleSize,int edgeNum,String spaceId);

    void importRandomTestDataStatic(long sampleSize);
}
