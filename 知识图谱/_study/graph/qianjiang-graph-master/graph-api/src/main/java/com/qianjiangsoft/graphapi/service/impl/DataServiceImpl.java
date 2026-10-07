package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.util.StrUtil;
import com.qianjiangsoft.graphapi.dto.VertexReturnDTO;
import com.qianjiangsoft.graphapi.param.*;
import com.qianjiangsoft.graphapi.service.DataService;
import com.qianjiangsoft.graphapi.service.TagService;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.mapper.GraphEdgeTypeMapper;
import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.model.Tag;
import com.qianjiangsoft.graphcore.service.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/4/28 9:36
 */
@Service
public class DataServiceImpl implements DataService {

    @Autowired
    GraphEdgeTypeMapper edgeTypeMapper;

    @Autowired
    EdgeService edgeService;

    @Autowired
    SpaceSyncService spaceSyncService;

    @Autowired
    TagService tagService;

    @Autowired
    VertexService vertexService;

    @Autowired
    SchemaService schemaService;

    @Autowired
    SpacePersistenceService spacePersistenceService;

    @Autowired
    SpaceService spaceService;

    @Override
    public void createEdge(List<CreateEdgeParam> edgeParamList, String edgeTypeName) {
        Space space = SpaceContextHolder.get();
        EdgeType edgeType = schemaService.findEdgeTypeByName(edgeTypeName, space.getId());

        edgeParamList.forEach(edgeParam -> {
            try {
                DateUtil.parse(edgeParam.getTime());
            } catch (Exception e) {
                edgeParam.setTime(null);
            }
            edgeService.createEdge(edgeType, edgeParam.getSourceVertexId()
                    , edgeParam.getDestinationVertexId(), edgeParam.getPropertyList(),
                    edgeParam.getStrength(), DateUtil.parse(edgeParam.getTime()), edgeParam.getCardList());
        });

        //更新时间
        spaceService.updateSpaceUpdateTime(space.getId());

        //同步空间
//        spaceSyncService.syncSpaceInfo(space.getId());
    }

    @Override
    public void createVertex(List<CreateVertexParam> vertexParamList, String tagName) {

        Space space = SpaceContextHolder.get();

        Tag tag = schemaService.findTagByName(tagName, space.getId());

        vertexParamList.forEach(vertexParam -> {
            vertexService.createVertex(tag, vertexParam.getId(), vertexParam.getName(), vertexParam.getPropertyList());
        });
    }

    @Override
    public void syncSpaceInfo(String spaceId) {
        Space space = SpaceContextHolder.get();

        spaceSyncService.syncSpaceInfo(spaceId);

        spacePersistenceService.saveSpace(space);
    }

    @Override
    public void updateVertex(UpdateVertexParam param) {
        vertexService.updateVertexProperty(param.getId(), param.getPropertyList());
    }

    @Override
    public void mergeVertex(MergeVertexParam param) {
        vertexService.mergeVertex(param.getId1(), param.getId2());
    }

    @Override
    public void deleteEdgeList(List<DeleteEdgeParam> paramList) {
        paramList.forEach(param -> {
            Space space = SpaceContextHolder.get();
            EdgeType edgeType = schemaService.findEdgeTypeByName(param.getEdgeTypeName(), space.getId());
            edgeService.deleteEdge(param.getSourceVertexId(), param.getDestinationVertexId(), edgeType);
        });
    }

    @Override
    public void deleteVertexById(String id) {
        vertexService.removeVertex(id);
    }

    @Override
    public void clearSpaceData() {
        spaceService.clearData(SpaceContextHolder.getSpaceId());
    }

    @Override
    public VertexReturnDTO findVertexById(String id) {
        return BeanUtil.copyProperties(vertexService.findVertexById(id), VertexReturnDTO.class);
    }

    @Override
    public void deleteEdgeByProperty(DeleteEdgeByPropertyParam param) {
        if(StrUtil.isNotEmpty(param.getPropertyName()) && StrUtil.isNotEmpty(param.getPropertyValue())) {
            edgeService.deleteEdgeByProperty(param.getPropertyName(), param.getPropertyValue());
        }
    }
}
