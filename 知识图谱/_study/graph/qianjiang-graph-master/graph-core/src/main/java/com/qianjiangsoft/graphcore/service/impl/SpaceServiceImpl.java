package com.qianjiangsoft.graphcore.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.json.JSONUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphcore.engine.GraphEngine;
import com.qianjiangsoft.graphcore.domain.entity.GraphEdgeType;
import com.qianjiangsoft.graphcore.domain.entity.GraphSpace;
import com.qianjiangsoft.graphcore.domain.entity.GraphTag;
import com.qianjiangsoft.graphcore.domain.entity.SchemaTemplate;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import com.qianjiangsoft.graphcore.mapper.GraphSpaceMapper;
import com.qianjiangsoft.graphcore.mapper.SchemaTemplateMapper;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.param.SelectSpacePageParam;
import com.qianjiangsoft.graphcore.service.SchemaService;
import com.qianjiangsoft.graphcore.service.SpacePersistenceService;
import com.qianjiangsoft.graphcore.service.SpaceService;
import com.qianjiangsoft.graphcore.service.SpaceSyncService;
import lombok.Data;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/2/24 9:38
 */
@Service
@Data
public class SpaceServiceImpl implements SpaceService {

    @Autowired
    GraphEngine graphEngine;

    @Autowired
    GraphSpaceMapper graphSpaceMapper;

    @Autowired
    SchemaTemplateMapper schemaTemplateMapper;

    @Autowired
    SchemaService schemaService;

    @Autowired
    SpaceSyncService spaceSyncService;

    @Autowired
    SpacePersistenceService spacePersistenceService;

    @Override
    public Space createSpace(String id, String name) {

        if(StrUtil.isEmpty(id)) {
            id = IdUtil.simpleUUID();
        }

        //check name
        LambdaQueryWrapper<GraphSpace> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(GraphSpace::getName, name);
        if (graphSpaceMapper.selectCount(queryWrapper) > 0) {
            throw new BusinessException(ResultCode.BUSINESS_DUPLICATE_RECORD_ERROR, "数据重复");
        }

        //check id
        //check name
        if (graphSpaceMapper.selectById(id) != null) {
            throw new BusinessException(ResultCode.BUSINESS_DUPLICATE_RECORD_ERROR, "ID重复");
        }

        GraphSpace graphSpace = new GraphSpace(id, name);
        graphSpace.setCreateTime(DateUtil.date());
        graphSpace.setUpdateTime(DateUtil.date());
        graphSpace.setLastVisitedTime(DateUtil.date());
        graphSpace.setActiveFlag(1);
        graphSpaceMapper.insert(graphSpace);

        Space space = new Space(graphSpace.getId(), graphSpace.getName());
        graphEngine.getSpaceMap().putIfAbsent(space.getId(), space);
        return space;
    }

    @Override
    public Space createSpaceByTemplate(String name, Integer templateId) {
        Space space = this.createSpace(null, name);

        //初始化数据
        SchemaTemplate template = schemaTemplateMapper.selectById(templateId);

        if(template == null) {
            return space;
        }

        //导入tag
        List<GraphTag> graphTagList = JSONUtil.toList(template.getTagList(), GraphTag.class);
        graphTagList.forEach(graphTag -> schemaService.createTag(space.getId(),
                graphTag.getName(), graphTag.getPropertyInfoList(), graphTag.getIconPath()));

        //导入edge type
        List<GraphEdgeType> graphEdgeTypeList = JSONUtil.toList(template.getEdgeTypeList(), GraphEdgeType.class);
        graphEdgeTypeList.forEach(graphEdgeType -> schemaService.createEdgeType(space.getId(), graphEdgeType.getName(),
                graphEdgeType.getPropertyInfoList()));

        return space;
    }

    @Override
    public Space selectSpaceById(String id) {
        return graphEngine.getSpaceMap().get(id);
    }


    @Override
    public void deleteSpace(String id) {
        //delete data
        schemaService.deleteTagBySpaceId(id);
        schemaService.deleteEdgeTpeBySpaceId(id);

        //清空文件
        spacePersistenceService.deleteSpaceFile(id);

        //移除空间
        graphEngine.getSpaceMap().remove(id);

        //删除空间
        graphSpaceMapper.deleteById(id);
    }

    @Override
    public List<GraphSpace> selectSpaceList(SelectSpacePageParam param) {
        return graphSpaceMapper.selectList(null);
    }

    @Override
    public void clearData(String spaceId) {
        Space space = graphEngine.getSpaceMap().get(spaceId);
        if(space != null) {
            space.getVertexList().clear();
            space.getEdgeList().clear();
            space.getVertexMap().clear();
        }
    }

    @Override
    public void unloadSpace(String spaceId) {
        //移除空间
        graphEngine.getSpaceMap().remove(spaceId);

        GraphSpace graphSpace = graphSpaceMapper.selectById(spaceId);

        //更新激活状态
        graphSpace.setActiveFlag(0);
        graphSpaceMapper.updateById(graphSpace);
    }

    @Override
    public void updateSpaceUpdateTime(String spaceId) {
        GraphSpace graphSpace = graphSpaceMapper.selectById(spaceId);
        graphSpace.setUpdateTime(DateUtil.date());
        graphSpaceMapper.updateById(graphSpace);
    }

    @Override
    public long countSpace() {
        return graphSpaceMapper.selectCount(null);
    }
}
