package com.qianjiangsoft.graphcore.helper;

import com.qianjiangsoft.graphcore.model.Edge;
import com.qianjiangsoft.graphcore.model.VertexStatisticsInfo;

import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/4/20 13:55
 */
public class StatisticsInfoHelper {

    public static String getMergeEdgeKey(Edge edge){
        String sourceId = edge.getSource().getId();
        String destId = edge.getDestination().getId();
        return sourceId + "_" + destId+ "_" + edge.getType().getName();
    }

    public static String getVertexStatisticsInfoKey(String vertexId, String edgeTypeName){
        return vertexId + "_" + edgeTypeName;
    }

    public static VertexStatisticsInfo getVertexStatisticsInfo(String vertexId, String edgeTypeName,
                                                 Map<String, VertexStatisticsInfo> vertexStatisticsInfoMap){
        String infoKey = StatisticsInfoHelper.getVertexStatisticsInfoKey(vertexId, edgeTypeName);

        //取出统计对象
        VertexStatisticsInfo vertexStatisticsInfo = vertexStatisticsInfoMap.get(infoKey);
        if(vertexStatisticsInfo == null) {
            vertexStatisticsInfo = new VertexStatisticsInfo();
            vertexStatisticsInfoMap.put(infoKey, vertexStatisticsInfo);
        }
        return vertexStatisticsInfo;
    }
}
