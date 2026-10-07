package com.qianjiangsoft.graphcore.service;

/**
 * @author marshallwu
 * @date 2022/2/24 9:35
 */
public interface SpaceSyncService {

    void syncSpaceInfo(String spaceId);

    void updateAdjacencyMap(String spaceId);

    void updateBaseStatisticsInfo(String spaceId);

    void updateMergeEdgeList(String spaceId);
}
