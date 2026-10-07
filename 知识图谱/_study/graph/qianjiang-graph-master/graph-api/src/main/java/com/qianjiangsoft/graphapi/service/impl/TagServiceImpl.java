package com.qianjiangsoft.graphapi.service.impl;


import com.qianjiangsoft.graphapi.param.CreateTagParam;
import com.qianjiangsoft.graphapi.param.UpdateTagParam;
import com.qianjiangsoft.graphapi.service.TagService;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.model.Tag;
import com.qianjiangsoft.graphcore.service.SchemaService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.List;


/**
 * @author ulmarr8861
 * @date 2022/3/18
 */
@Service
@RequiredArgsConstructor
public class TagServiceImpl implements TagService {

    private final SchemaService schemaService;

    @Override
    public void create(CreateTagParam param) {
       Space space =  SpaceContextHolder.get();
        schemaService.createTag(space.getId(), param.getName(), param.getPropertyInfoList(), param.getIconPath());
    }

    @Override
    public void delete(String id) {
        schemaService.deleteTagById(id);
    }

    @Override
    public void update(UpdateTagParam param) {
        schemaService.updateTagById(param.getId(), param.getName(), param.getPropertyInfoList(),param.getIconPath());
    }

    @Override
    public List<Tag> selectBySpaceId(String spaceId) {
        return schemaService.selectTagListBySpaceId(spaceId);
    }

    @Override
    public void createBatchTags(List<CreateTagParam>  tagList) {
        Space space = SpaceContextHolder.get();
        tagList.forEach(tag -> {
            schemaService.createTag(space.getId(),tag.getName(), tag.getPropertyInfoList(), tag.getIconPath());
        });
    }

    @Override
    public void deleteAllTag() {
        schemaService.deleteTagBySpaceId(SpaceContextHolder.getSpaceId());
    }
}
