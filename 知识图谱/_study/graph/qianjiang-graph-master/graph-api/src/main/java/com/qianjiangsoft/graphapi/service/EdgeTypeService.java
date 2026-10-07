package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphapi.dto.EdgeTypeReturnDTO;
import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphapi.param.CreateEdgeTypeParam;
import com.qianjiangsoft.graphcore.param.UpdateEdgeTypeParam;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/30
 */
public interface EdgeTypeService {

    List<EdgeTypeReturnDTO> selectEdgeTypeListBySpaceId(String spaceId);

    void batchCreate(List<CreateEdgeTypeParam> edgeTypeList);

     EdgeType createEdgeType(CreateEdgeTypeParam param);

    void updateEdgeTypeById(UpdateEdgeTypeParam param);

    void deleteEdgeTypeById(String id);

    void deleteAllEdgeTpe();
}
