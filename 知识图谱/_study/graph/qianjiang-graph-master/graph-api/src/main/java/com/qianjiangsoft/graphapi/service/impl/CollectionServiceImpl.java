package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.bean.BeanUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.qianjiangsoft.graphapi.dto.CollectionListDTO;
import com.qianjiangsoft.graphapi.param.CreateCollectionParam;
import com.qianjiangsoft.graphapi.service.CollectionService;
import com.qianjiangsoft.graphcore.domain.entity.GraphCollection;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.mapper.GraphCollectionMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class CollectionServiceImpl implements CollectionService {

    @Autowired
    GraphCollectionMapper collectionMapper;

    @Override
    public void batchCreateCollection(List<CreateCollectionParam> paramList) {
        String spaceId = SpaceContextHolder.getSpaceId();

        for (CreateCollectionParam param : paramList) {
            GraphCollection collection = BeanUtil.copyProperties(param, GraphCollection.class);
            collection.setSpaceId(spaceId);
            collectionMapper.insert(collection);
        }
    }

    @Override
    public List<CollectionListDTO> selectCollectionList() {
        LambdaQueryWrapper<GraphCollection> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(GraphCollection::getSpaceId, SpaceContextHolder.getSpaceId());
        List<GraphCollection> collectionList = collectionMapper.selectList(queryWrapper);
        return BeanUtil.copyToList(collectionList, CollectionListDTO.class);
    }

    @Override
    public void deleteAll() {
        LambdaQueryWrapper<GraphCollection> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(GraphCollection::getSpaceId, SpaceContextHolder.getSpaceId());
        collectionMapper.delete(queryWrapper);
    }
}
