package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.bean.BeanUtil;
import com.qianjiangsoft.graphapi.dto.EdgeTypeReturnDTO;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphapi.param.CreateEdgeTypeParam;
import com.qianjiangsoft.graphcore.param.UpdateEdgeTypeParam;
import com.qianjiangsoft.graphapi.service.EdgeTypeService;
import com.qianjiangsoft.graphcore.service.SchemaService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/30
 */
@Service
@RequiredArgsConstructor
public class EdgeTypeServiceImpl implements EdgeTypeService {
    private final SchemaService schemaService;

    @Override
    public List<EdgeTypeReturnDTO> selectEdgeTypeListBySpaceId(String spaceId) {

        List<EdgeTypeReturnDTO> edgeTypeReturnDTOList = BeanUtil.copyToList(schemaService.selectEdgeTypeListBySpaceId(spaceId),
                EdgeTypeReturnDTO.class);

        return edgeTypeReturnDTOList;
    }


    @Override
    public void batchCreate(List<CreateEdgeTypeParam> edgeTypeList) {
        edgeTypeList.forEach(edgeType -> {
            schemaService.createEdgeType(edgeType.getName(), edgeType.getPropertyInfoList());
        });
    }

    @Override
    public EdgeType createEdgeType(CreateEdgeTypeParam param) {
        return schemaService.createEdgeType(param.getName(), param.getPropertyInfoList());
    }

    @Override
    public void updateEdgeTypeById(UpdateEdgeTypeParam param) {
        schemaService.updateEdgeTypeById(param);
    }

    @Override
    public void deleteEdgeTypeById(String id) {
        schemaService.deleteEdgeTypeById(id);
    }

    @Override
    public void deleteAllEdgeTpe() {
        schemaService.deleteEdgeTpeBySpaceId(SpaceContextHolder.get().getId());
    }
}
