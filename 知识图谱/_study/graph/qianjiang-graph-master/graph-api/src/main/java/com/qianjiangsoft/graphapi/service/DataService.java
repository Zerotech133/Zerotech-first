package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphapi.dto.VertexReturnDTO;
import com.qianjiangsoft.graphapi.param.*;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/4/28 9:35
 */
public interface DataService {

    /**
     * 当前space下批量创建edge
     * @param: edgeParamList
     * @param: edgeTypeId
     * @return: void
     * @author: marshall
     * @date: 2022/3/29
     */
    void createEdge(List<CreateEdgeParam> edgeParamList, String edgeTypeId);

    void createVertex(List<CreateVertexParam> vertexParamList, String tagId);

    void syncSpaceInfo(String spaceId);

    void updateVertex(UpdateVertexParam param);

    void mergeVertex(MergeVertexParam param);

    void deleteEdgeList(List<DeleteEdgeParam> paramList);

    void deleteVertexById(String id);

    void clearSpaceData();

    VertexReturnDTO findVertexById(String id);

    void deleteEdgeByProperty(DeleteEdgeByPropertyParam param);
}
