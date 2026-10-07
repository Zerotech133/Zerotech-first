package com.qianjiangsoft.graphcore.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.util.IdUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.domain.entity.GraphEdgeType;
import com.qianjiangsoft.graphcore.domain.entity.GraphTag;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import com.qianjiangsoft.graphcore.mapper.GraphEdgeTypeMapper;
import com.qianjiangsoft.graphcore.mapper.GraphTagMapper;
import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.model.PropertyInfo;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.model.Tag;
import com.qianjiangsoft.graphcore.param.UpdateEdgeTypeParam;
import com.qianjiangsoft.graphcore.service.SchemaService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;

/**
 * @author marshallwu
 * @date 2022/2/24 10:44
 */
@Service
public class SchemaServiceImpl implements SchemaService {

    @Autowired
    GraphTagMapper graphTagMapper;

    @Autowired
    GraphEdgeTypeMapper graphEdgeTypeMapper;

    @Override
    public Tag createTag(String name, List<PropertyInfo> propertyInfoList, String iconPath) {
        return createTag(SpaceContextHolder.get().getId(), name, propertyInfoList, iconPath);
    }

    @Override
    public Tag createTag(String spaceId, String name, List<PropertyInfo> propertyInfoList, String iconPath) {

        GraphTag tag = new GraphTag(IdUtil.simpleUUID(), name, propertyInfoList, spaceId, iconPath);

        //check name
        LambdaQueryWrapper<GraphTag> queryWrapper = new LambdaQueryWrapper();
        queryWrapper.eq(GraphTag::getName, name)
                .eq(GraphTag::getSpaceId, spaceId);
        if(graphTagMapper.selectCount(queryWrapper) > 0) {
            throw new BusinessException(ResultCode.BUSINESS_DUPLICATE_RECORD_ERROR, "重复数据");
        }

        graphTagMapper.insert(tag);
        return BeanUtil.copyProperties(tag, Tag.class);
    }

    @Override
    public EdgeType createEdgeType(String name, List<PropertyInfo> propertyInfoList) {

        Space space = SpaceContextHolder.get();
        return createEdgeType(space.getId(), name, propertyInfoList);
    }


    @Override
    public EdgeType createEdgeType(String spaceId, String name, List<PropertyInfo> propertyInfoList) {

        GraphEdgeType edgeType = new GraphEdgeType(IdUtil.simpleUUID(), name, propertyInfoList, spaceId);

        //check name
        LambdaQueryWrapper<GraphEdgeType> queryWrapper = new LambdaQueryWrapper();
        queryWrapper.eq(GraphEdgeType::getName, name)
                .eq(GraphEdgeType::getSpaceId, spaceId);
        if(graphEdgeTypeMapper.selectCount(queryWrapper) > 0) {
            throw new BusinessException(ResultCode.BUSINESS_DUPLICATE_RECORD_ERROR, "重复数据");
        }

        graphEdgeTypeMapper.insert(edgeType);
        return BeanUtil.copyProperties(edgeType, EdgeType.class);
    }

    @Override
    public List<Tag> selectTagListBySpaceId(String spaceId) {

        LambdaQueryWrapper<GraphTag> queryWrapper = new LambdaQueryWrapper();
        queryWrapper.eq(GraphTag::getSpaceId, spaceId);
        List<GraphTag> graphTags = graphTagMapper.selectList(queryWrapper);
        List<Tag> tags = new ArrayList<>();
        graphTags.forEach(graphTag -> tags.add(BeanUtil.copyProperties(graphTag, Tag.class)));
        return tags;
    }

    @Override
    public List<EdgeType> selectEdgeTypeListBySpaceId(String spaceId) {

        LambdaQueryWrapper<GraphEdgeType> queryWrapper = new LambdaQueryWrapper();
        queryWrapper.eq(GraphEdgeType::getSpaceId, spaceId);
        return BeanUtil.copyToList(graphEdgeTypeMapper.selectList(queryWrapper), EdgeType.class);
    }

    @Override
    public void updateEdgeTypeById(UpdateEdgeTypeParam param) {
        LambdaQueryWrapper<GraphEdgeType> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(GraphEdgeType::getId, param.getId());
        if (graphEdgeTypeMapper.selectCount(queryWrapper)==0){
            throw new BusinessException(ResultCode.PARAM_ERROR, "查无此数据");
        }
        GraphEdgeType graphEdgeType = BeanUtil.copyProperties(param, GraphEdgeType.class);
        graphEdgeTypeMapper.updateById(graphEdgeType);
    }

    @Override
    public Tag findTagByName(String name, String spaceId) {
        LambdaQueryWrapper<GraphTag> queryWrapper = new LambdaQueryWrapper();
        queryWrapper.eq(GraphTag::getSpaceId, spaceId)
                .eq(GraphTag::getName, name);

        GraphTag graphTag = graphTagMapper.selectOne(queryWrapper);
        if(graphTag == null) {
            return null;
        }else {
            return BeanUtil.copyProperties(graphTag, Tag.class);
        }

    }

    @Override
    public EdgeType findEdgeTypeByName(String name, String spaceId) {
        LambdaQueryWrapper<GraphEdgeType> queryWrapper = new LambdaQueryWrapper();
        queryWrapper.eq(GraphEdgeType::getSpaceId, spaceId)
                .eq(GraphEdgeType::getName, name);

        GraphEdgeType graphEdgeType = graphEdgeTypeMapper.selectOne(queryWrapper);

        if(graphEdgeType == null) {
            return null;
        }else {
            return BeanUtil.copyProperties(graphEdgeType, EdgeType.class);
        }
    }

    @Override
    public void deleteTagBySpaceId(String spaceId) {
        LambdaQueryWrapper<GraphTag> deleteWrapper = new LambdaQueryWrapper();
        deleteWrapper.eq(GraphTag::getSpaceId, spaceId);
        graphTagMapper.delete(deleteWrapper);
    }

    @Override
    public void deleteEdgeTpeBySpaceId(String spaceId) {
        LambdaQueryWrapper<GraphEdgeType> deleteWrapper = new LambdaQueryWrapper();
        deleteWrapper.eq(GraphEdgeType::getSpaceId, spaceId);
        graphEdgeTypeMapper.delete(deleteWrapper);
    }

    @Override
    public void deleteEdgeTypeById(String id) {
        graphEdgeTypeMapper.deleteById(id);
    }

    @Override
    public Tag selectTagById(String id) {
        return BeanUtil.copyProperties(graphTagMapper.selectById(id), Tag.class);
    }

    @Override
    public void updateTagById(String id, String name, List<PropertyInfo> propertyInfoList, String iconPath) {
        //check id
        LambdaQueryWrapper<GraphTag> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(GraphTag::getId, id);
        if (graphTagMapper.selectCount(queryWrapper) == 0) {
            throw new BusinessException(ResultCode.BUSINESS_ERROR, "查无此数据");
        }

        GraphTag graphTag = new GraphTag();
        graphTag.setId(id);
        graphTag.setName(name);
        graphTag.setPropertyInfoList(propertyInfoList);
        graphTag.setIconPath(iconPath);
        graphTagMapper.updateById(graphTag);
    }

    @Override
    public void deleteTagById(String id) {
        graphTagMapper.deleteById(id);
    }
}
