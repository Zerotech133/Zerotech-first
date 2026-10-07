package com.qianjiangsoft.graphcore.service;

import com.qianjiangsoft.graphcore.model.Edge;
import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.model.Vertex;
import com.qianjiangsoft.graphcore.param.MergeEdgeParam;

import java.util.Date;
import java.util.List;
import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/2/24 11:29
 */
public interface EdgeService {

    /**
     * 在当前space下创建一条边
     * @param: name
     * @param: source
     * @param: destination
     * @param: type
     * @param: propertyList
     * @return: com.qianjiangsoft.graphcore.model.Edge
     * @author: marshall
     * @date: 2022/3/14
    */
    Edge createEdge(EdgeType type, Vertex source, Vertex destination,
                    Map<String, Object> propertyList, Double strength, Date time, Map<String, String> cardList);



    void createEdge(EdgeType type, String sourceId, String destinationId,
                    Map<String, Object> propertyList, Double strength, Date time, Map<String, String> cardList);

    List<Edge> queryEdgeListByMergeEdge(MergeEdgeParam mergeEdge);

    void deleteEdge(String sourceId, String destinationId, EdgeType type);

    void deleteEdgeByProperty(String propertyName, String propertyValue);
}
